from __future__ import annotations

import json
import logging
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

log = logging.getLogger(__name__)

from contracts import ReviewResult, Story, TopicAttachment
from settings import RuntimeSettings
from utils import clean_whitespace, project_root, safe_filename, similarity


def _utc_now() -> datetime:
    return datetime.now(timezone.utc).replace(microsecond=0)


def _parse_time(value: str | None) -> datetime | None:
    if not value:
        return None
    try:
        if value.endswith("Z"):
            value = value[:-1] + "+00:00"
        parsed = datetime.fromisoformat(value)
        if parsed.tzinfo is None:
            return parsed.replace(tzinfo=timezone.utc)
        return parsed.astimezone(timezone.utc)
    except ValueError:
        return None


def _token_overlap(a: str, b: str) -> float:
    a_tokens = {token for token in safe_filename(a).split("-") if len(token) > 2}
    b_tokens = {token for token in safe_filename(b).split("-") if len(token) > 2}
    if not a_tokens or not b_tokens:
        return 0.0
    return len(a_tokens & b_tokens) / len(a_tokens | b_tokens)


def _topic_id(name: str, created_at: datetime) -> str:
    date_part = created_at.strftime("%Y%m%d")
    return f"{date_part}-{safe_filename(name)[:48] or 'topic'}"


def _story_time(story: Story, review: ReviewResult | None = None) -> datetime | None:
    return (
        _parse_time(story.get("published_at"))
        or _parse_time(story.get("collected_at"))
        or _parse_time(review.get("reviewed_at") if review else None)
    )


def _mention_overlap(a: list[str], b: list[str]) -> float:
    a_set = {clean_whitespace(value).lower() for value in a if clean_whitespace(value)}
    b_set = {clean_whitespace(value).lower() for value in b if clean_whitespace(value)}
    if not a_set or not b_set:
        return 0.0
    return len(a_set & b_set) / len(a_set | b_set)


def _criteria_overlap(review: ReviewResult, topic: dict[str, Any]) -> float:
    """Score boost when the new review shares high-scoring criteria with existing topic items."""
    review_scores = {
        cs["criterion"]: cs["score"]
        for cs in review.get("criteria_scores", [])
        if cs.get("score", 0) > 5
    }
    if not review_scores:
        return 0.0

    topic_high_criteria: set[str] = set()
    for item in topic.get("items", []):
        for cs in item.get("criteria_scores", []):
            if cs.get("score", 0) > 5:
                topic_high_criteria.add(cs["criterion"])

    if not topic_high_criteria:
        return 0.0

    shared = set(review_scores.keys()) & topic_high_criteria
    possible = set(review_scores.keys()) | topic_high_criteria
    return len(shared) / len(possible) if possible else 0.0


class TopicMemory:
    def __init__(self, settings: RuntimeSettings, path: Path | None = None) -> None:
        self.settings = settings
        self.path = path or settings.topics_path or project_root() / "data" / "topics.json"
        self.topics: list[dict[str, Any]] = []

    @classmethod
    def load(cls, settings: RuntimeSettings, path: Path | None = None) -> "TopicMemory":
        memory = cls(settings, path)
        if memory.path.exists():
            data = json.loads(memory.path.read_text(encoding="utf-8"))
            if isinstance(data, dict):
                memory.topics = data.get("topics", [])
            elif isinstance(data, list):
                memory.topics = data
        return memory

    def save(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        now = _utc_now()
        active_topics = [
            t for t in self.topics
            if not _parse_time(t.get("window_end")) or _parse_time(t.get("window_end")) >= now
        ]
        pruned = len(self.topics) - len(active_topics)
        if pruned:
            log.info("Pruned %d expired topic(s) from memory.", pruned)
        self.topics = active_topics
        payload = {
            "schema_version": 1,
            "topic_window_days": self.settings.topic_window_days,
            "topics": self.topics,
        }
        self.path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")

    def attach(self, story: Story, review: ReviewResult) -> TopicAttachment:
        now = _utc_now()
        name = clean_whitespace(review.get("topic_hint") or story.get("title") or "Untitled topic")
        topic = self._find_active_topic(name, story, review, now)
        status = "existing"

        if topic is None:
            status = "new"
            topic = self._new_topic(name, now)
            self.topics.append(topic)

        sources = topic.setdefault("sources", [])
        if story.get("source") and story["source"] not in sources:
            sources.append(story["source"])

        mentions = topic.setdefault("mentions", [])
        for mention in review.get("mentions", []):
            if mention not in mentions:
                mentions.append(mention)

        item = {
            "story_id": story["id"],
            "source": story.get("source", "unknown"),
            "title": story.get("title", ""),
            "url": story.get("url", ""),
            "published_at": story.get("published_at"),
            "collected_at": story.get("collected_at"),
            "reviewed_at": review.get("reviewed_at"),
            "priority": review.get("priority"),
            "summary": review.get("summary"),
            "claims_to_verify": review.get("claims_to_verify", []),
            "mentions": review.get("mentions", []),
            "criteria_scores": review.get("criteria_scores", []),
        }

        existing_ids = {entry.get("story_id") for entry in topic.setdefault("items", [])}
        if story["id"] not in existing_ids:
            topic["items"].append(item)

        topic["updated_at"] = now.isoformat()
        topic["source_count"] = len(topic.get("sources", []))
        topic["item_count"] = len(topic.get("items", []))
        topic["highest_priority"] = _highest_priority(topic.get("items", []))

        return {
            "topic": topic,
            "topic_status": status,
            "cross_check": self._cross_check(topic, story),
        }

    def _find_active_topic(
        self,
        name: str,
        story: Story,
        review: ReviewResult,
        now: datetime,
    ) -> dict[str, Any] | None:
        best_topic = None
        best_score = 0.0
        story_time = _story_time(story, review)

        for topic in self.topics:
            window_end = _parse_time(topic.get("window_end"))
            if window_end and now > window_end:
                continue

            score = self._topic_match_score(topic, name, story, review, story_time)

            if score > best_score:
                best_score = score
                best_topic = topic

        if best_score >= self.settings.topic_match_threshold:
            return best_topic
        return None

    def _topic_match_score(
        self,
        topic: dict[str, Any],
        name: str,
        story: Story,
        review: ReviewResult,
        story_time: datetime | None,
    ) -> float:
        topic_name = clean_whitespace(topic.get("name", ""))
        title_score = similarity(name, topic_name)
        hint_overlap = _token_overlap(name, topic_name)
        title_overlap = _token_overlap(story.get("title", ""), topic_name)
        url_overlap = _token_overlap(story.get("url", ""), topic_name)
        mention_score = _mention_overlap(review.get("mentions", []), topic.get("mentions", []))
        topic_time = _parse_time(topic.get("updated_at")) or _parse_time(topic.get("created_at"))
        time_score = 0.0
        if story_time and topic_time:
            delta_hours = abs((story_time - topic_time).total_seconds()) / 3600
            if delta_hours <= 24:
                time_score = 1.0
            elif delta_hours <= 72:
                time_score = 0.7
            elif delta_hours <= 168:
                time_score = 0.4

        criteria_score = _criteria_overlap(review, topic)

        return round(
            (0.35 * max(title_score, hint_overlap))
            + (0.20 * max(title_overlap, url_overlap))
            + (0.22 * mention_score)
            + (0.13 * time_score)
            + (0.10 * criteria_score),
            3,
        )

    def _new_topic(self, name: str, now: datetime) -> dict[str, Any]:
        window_end = now + timedelta(days=self.settings.topic_window_days)
        return {
            "id": _topic_id(name, now),
            "name": name,
            "created_at": now.isoformat(),
            "updated_at": now.isoformat(),
            "window_start": now.isoformat(),
            "window_end": window_end.isoformat(),
            "sources": [],
            "mentions": [],
            "items": [],
            "source_count": 0,
            "item_count": 0,
            "highest_priority": "low",
        }

    def _cross_check(self, topic: dict[str, Any], story: dict[str, Any]) -> dict[str, Any]:
        other_items = [item for item in topic.get("items", []) if item.get("story_id") != story.get("id")]
        other_sources = sorted({item.get("source", "unknown") for item in other_items})
        return {
            "status": "new_report" if not other_items else "seen_before",
            "other_source_count": len(other_sources),
            "other_sources": other_sources,
            "related_item_count": len(other_items),
            "topic_window_end": topic.get("window_end"),
        }


def _highest_priority(items: list[dict[str, Any]]) -> str:
    order = {"ignore": 0, "low": 1, "medium": 2, "high": 3, "breaking": 4}
    highest = "low"
    for item in items:
        priority = item.get("priority", "low")
        if order.get(priority, 0) > order.get(highest, 0):
            highest = priority
    return highest
