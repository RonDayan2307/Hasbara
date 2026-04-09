from __future__ import annotations

import json
import os
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

from utils import clean_whitespace, project_root, safe_filename, similarity

TOPIC_WINDOW_DAYS = int(os.getenv("NEWS_TOPIC_WINDOW_DAYS", "14"))
MATCH_THRESHOLD = float(os.getenv("NEWS_TOPIC_MATCH_THRESHOLD", "0.52"))


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


class TopicMemory:
    def __init__(self, path: Path | None = None) -> None:
        self.path = path or project_root() / "data" / "topics.json"
        self.topics: list[dict[str, Any]] = []

    @classmethod
    def load(cls, path: Path | None = None) -> "TopicMemory":
        memory = cls(path)
        if memory.path.exists():
            data = json.loads(memory.path.read_text(encoding="utf-8"))
            if isinstance(data, dict):
                memory.topics = data.get("topics", [])
            elif isinstance(data, list):
                memory.topics = data
        return memory

    def save(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        payload = {
            "schema_version": 1,
            "topic_window_days": TOPIC_WINDOW_DAYS,
            "topics": self.topics,
        }
        self.path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")

    def attach(self, story: dict[str, Any], review: dict[str, Any]) -> dict[str, Any]:
        now = _utc_now()
        name = clean_whitespace(review.get("topic_hint") or story.get("title") or "Untitled topic")
        topic = self._find_active_topic(name, now)
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
            "one_sentence_summary": review.get("one_sentence_summary"),
            "claims_to_verify": review.get("claims_to_verify", []),
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

    def _find_active_topic(self, name: str, now: datetime) -> dict[str, Any] | None:
        best_topic = None
        best_score = 0.0

        for topic in self.topics:
            window_end = _parse_time(topic.get("window_end"))
            if window_end and now > window_end:
                continue

            topic_name = topic.get("name", "")
            title_score = similarity(name, topic_name)
            overlap_score = _token_overlap(name, topic_name)
            score = max(title_score, overlap_score)

            if score > best_score:
                best_score = score
                best_topic = topic

        if best_score >= MATCH_THRESHOLD:
            return best_topic
        return None

    def _new_topic(self, name: str, now: datetime) -> dict[str, Any]:
        window_end = now + timedelta(days=TOPIC_WINDOW_DAYS)
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
