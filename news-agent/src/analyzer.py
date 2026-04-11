from __future__ import annotations

import json
import logging
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from criteria import ReviewCriterion, load_review_criteria
from ollama_client import OllamaClient
from report_renderer import render_report_from_reviews  # re-exported for backward compat
from review_cache import ReviewCache
from settings import RuntimeSettings
from utils import clean_whitespace

log = logging.getLogger(__name__)


def _utc_now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


class LocalAiAnalyzer:
    def __init__(self, settings: RuntimeSettings) -> None:
        self.settings = settings
        self.criteria = load_review_criteria(settings.criteria_path)
        self.client = OllamaClient(settings)
        self.cache = ReviewCache.load(settings.review_cache_path)

    # ── Public API ────────────────────────────────────────────────────────────

    def health_check(self) -> bool:
        return self.client.health_check()

    def review_story(self, story: dict[str, Any]) -> dict[str, Any]:
        story_id = story.get("id", "")
        if story_id:
            cached = self.cache.get(story_id)
            if cached is not None:
                log.info(
                    "Cache hit — skipping Ollama for: %s",
                    story.get("title", story_id)[:80],
                )
                return cached

        review = self._review_single_story_with_fallback(story, reason="sequential review")

        if story_id and review.get("review_method") == "model":
            self.cache.put(story_id, review)

        return review

    def synthesize_report(self, reviewed_items: list[dict[str, Any]]) -> str:
        if self.settings.report_mode != "model":
            return render_report_from_reviews(
                reviewed_items, self.criteria, model_name=self.settings.local_ai_model
            )

        if not reviewed_items:
            return "No articles were reviewed in this run."

        messages = [
            {
                "role": "system",
                "content": (
                    "You write concise, neutral monitoring reports from structured article reviews. "
                    "You preserve uncertainty and do not add facts."
                ),
            },
            {"role": "user", "content": self._build_report_prompt(reviewed_items)},
        ]
        return self.client.chat(messages, num_predict=self.settings.report_num_predict)

    # ── Review pipeline ───────────────────────────────────────────────────────

    def _review_single_story_with_fallback(
        self, story: dict[str, Any], *, reason: str
    ) -> dict[str, Any]:
        log.info(
            "Reviewing: %s",
            story.get("title", story.get("id", "untitled"))[:80],
        )
        review, failure_reason = self._review_single_story(story)
        if review is not None:
            return review

        log.warning(
            "Falling back to heuristic for %s: %s",
            story.get("title", story.get("id", "untitled"))[:60],
            failure_reason or reason,
        )
        return self._fallback_review(story, reason=failure_reason or reason)

    def _review_single_story(
        self, story: dict[str, Any]
    ) -> tuple[dict[str, Any] | None, str]:
        messages = [
            {
                "role": "system",
                "content": (
                    "You are a careful article triage analyst. You score defined criteria, summarize "
                    "faithfully, and never invent facts."
                ),
            },
            {"role": "user", "content": self._build_single_review_prompt(story)},
        ]
        try:
            raw_text = self.client.chat(
                messages,
                num_predict=max(120, self.settings.review_num_predict_per_story),
                json_format=True,
            )
        except Exception as exc:
            log.warning("Single-story review failed for %s: %s", story.get("id"), exc)
            return None, str(exc)

        if not raw_text.strip():
            log.warning("Model returned empty output for %s.", story.get("id"))
            self._write_debug_event(
                story.get("id", "untitled"),
                "single_empty_response",
                {
                    "reason": "empty primary response",
                    "title": story.get("title"),
                    "url": story.get("url"),
                },
            )
            return self._review_single_story_compact(story, reason="empty primary response")

        try:
            raw_json = _json_from_text(raw_text)
        except json.JSONDecodeError as exc:
            debug_path = self._write_debug_response(
                story.get("id", "untitled"), "single_invalid_json", raw_text
            )
            log.warning(
                "Could not parse JSON for %s: %s. Saved to %s", story.get("id"), exc, debug_path
            )
            return self._review_single_story_compact(story, reason="invalid primary JSON")

        return self._normalize_review(self._extract_single_review(raw_json), story), ""

    def _review_single_story_compact(
        self, story: dict[str, Any], *, reason: str
    ) -> tuple[dict[str, Any] | None, str]:
        messages = [
            {
                "role": "system",
                "content": (
                    "You are a careful article triage analyst. Return short strict JSON only. "
                    "Never add markdown or explanatory text outside the JSON object."
                ),
            },
            {"role": "user", "content": self._build_compact_single_review_prompt(story)},
        ]
        try:
            raw_text = self.client.chat(
                messages,
                num_predict=max(90, self.settings.review_num_predict_per_story),
                json_format=True,
            )
        except Exception as exc:
            log.warning("Compact retry failed for %s: %s", story.get("id"), exc)
            return None, f"{reason}; compact retry failed: {exc}"

        if not raw_text.strip():
            log.warning("Compact retry also returned empty for %s.", story.get("id"))
            self._write_debug_event(
                story.get("id", "untitled"),
                "compact_empty_response",
                {"reason": reason, "title": story.get("title"), "url": story.get("url")},
            )
            return None, f"{reason}; compact retry returned empty output"

        try:
            raw_json = _json_from_text(raw_text)
        except json.JSONDecodeError as exc:
            debug_path = self._write_debug_response(
                story.get("id", "untitled"), "compact_invalid_json", raw_text
            )
            log.warning(
                "Could not parse compact JSON for %s: %s. Saved to %s",
                story.get("id"),
                exc,
                debug_path,
            )
            return None, f"{reason}; compact retry returned invalid JSON"

        return self._normalize_review(self._extract_single_review(raw_json), story), ""

    # ── Prompt builders ───────────────────────────────────────────────────────

    def _build_single_review_prompt(self, story: dict[str, Any]) -> str:
        story_block = _story_prompt_block(story, self.settings.max_body_chars)
        return f"""
Review this media article for a source-faithful public diplomacy monitoring workflow.

For each criterion below, answer the question and score it from 1 to 10:
{_criteria_prompt(self.criteria)}

Important rules:
- Use only the provided article text and metadata.
- Do not decide that a claim is false unless the article itself proves that.
- Treat high misinformation scores as "needs verification", not as a final truth verdict.
- Return JSON only. No markdown.
- Return exactly one criteria_scores entry for each criterion listed above.
- topic_hint must be a short topic label of 2 to 6 words.
- summary must be one neutral sentence and should not repeat topic_hint.

JSON schema:
{{
  "story_id": "story id here",
  "criteria_scores": [
    {{
      "criterion": "criterion_name",
      "score": 1,
      "reason": "short evidence-based reason"
    }}
  ],
  "topic_hint": "short topic label",
  "summary": "one neutral sentence",
  "claims_to_verify": ["specific claims that should be checked"],
  "review_reason": "why this is or is not worth review",
  "confidence": "high | medium | low"
}}

Story:
{json.dumps(story_block, ensure_ascii=False, indent=2)}
""".strip()

    def _build_compact_single_review_prompt(self, story: dict[str, Any]) -> str:
        story_block = _story_prompt_block(story, min(self.settings.max_body_chars, 900))
        criteria = ", ".join(criterion.name for criterion in self.criteria)
        return f"""
Review this article and return compact JSON only.

Required fields:
- story_id, topic_hint, summary, claims_to_verify, review_reason, confidence, criteria_scores

For criteria_scores, return an object where each key is a criterion name and each value is an integer 1-10.

Criteria: {criteria}

JSON shape:
{{
  "story_id": "story id here",
  "topic_hint": "short topic label",
  "summary": "one neutral sentence",
  "claims_to_verify": ["specific claims"],
  "review_reason": "why this is or is not worth review",
  "confidence": "high | medium | low",
  "criteria_scores": {{"criterion_name": 1}}
}}

Story:
{json.dumps(story_block, ensure_ascii=False, indent=2)}
""".strip()

    def _build_report_prompt(self, reviewed_items: list[dict[str, Any]]) -> str:
        compact_items = []
        for item in reviewed_items:
            story = item["story"]
            review = item["review"]
            topic = item.get("topic", {})
            compact_items.append(
                {
                    "source": story.get("source"),
                    "title": story.get("title"),
                    "url": story.get("url"),
                    "priority": review.get("priority"),
                    "topic": topic.get("name") or review.get("topic_hint"),
                    "topic_status": item.get("topic_status"),
                    "summary": review.get("summary"),
                    "criteria_scores": review.get("criteria_scores", []),
                    "claims_to_verify": review.get("claims_to_verify", [])[:4],
                }
            )

        return f"""
Create a concise source-faithful monitoring report from these reviewed articles.

Rules:
- Use only the reviewed article data below.
- Group related items by topic.
- Make clear what is confirmed by multiple sources and what is only a single-source claim.
- Do not add recommendations, slogans, or invented context.
- Keep the report practical for a human analyst.

Reviewed data:
{json.dumps(compact_items, ensure_ascii=False, indent=2)}
""".strip()

    # ── Normalization ─────────────────────────────────────────────────────────

    def _extract_single_review(self, raw_json: Any) -> dict[str, Any]:
        if isinstance(raw_json, dict):
            review = raw_json.get("review")
            if isinstance(review, dict):
                return review
            reviews = raw_json.get("reviews")
            if isinstance(reviews, list):
                for entry in reviews:
                    if isinstance(entry, dict):
                        return entry
            return raw_json
        if isinstance(raw_json, list):
            for entry in raw_json:
                if isinstance(entry, dict):
                    return entry
        return {}

    def _normalize_review(self, raw: dict[str, Any], story: dict[str, Any]) -> dict[str, Any]:
        criteria_scores = self._normalize_criteria_scores(raw.get("criteria_scores"), story)
        score_values = [entry["score"] for entry in criteria_scores]
        max_score = max(score_values) if score_values else 1
        average_score = round(sum(score_values) / len(score_values), 1) if score_values else 1.0
        review_method = clean_whitespace(str(raw.get("review_method") or "model")).lower()

        if review_method == "heuristic_fallback":
            worth_reviewing = (
                max_score >= max(self.settings.priority_breaking_min_score, 9)
                and average_score >= max(self.settings.review_average_min_score, 4)
            )
            priority = "low" if worth_reviewing else "ignore"
        else:
            worth_reviewing = (
                max_score >= self.settings.review_worthy_min_score
                or average_score >= self.settings.review_average_min_score
            )
            if max_score >= self.settings.priority_breaking_min_score:
                priority = "breaking"
            elif max_score >= self.settings.priority_high_min_score:
                priority = "high"
            elif worth_reviewing:
                priority = "medium"
            else:
                priority = "ignore"

        return {
            "story_id": story["id"],
            "reviewed_at": _utc_now(),
            "model": self.settings.local_ai_model,
            "review_method": review_method,
            "worth_reviewing": worth_reviewing,
            "priority": priority,
            "criteria_scores": criteria_scores,
            "score_summary": {"max_score": max_score, "average_score": average_score},
            "source_language": clean_whitespace(str(story.get("source_language") or "unknown")),
            "political_orientation": clean_whitespace(
                str(story.get("source_orientation") or "unknown")
            ),
            "mentions": _mentions_from_text(story),
            "topic_hint": _normalize_topic_hint(raw.get("topic_hint"), story),
            "summary": _normalize_summary(raw.get("summary"), story),
            "claims_to_verify": _as_list(raw.get("claims_to_verify"))[:6],
            "review_reason": clean_whitespace(str(raw.get("review_reason") or "")),
            "confidence": clean_whitespace(str(raw.get("confidence") or "medium")).lower(),
        }

    def _normalize_criteria_scores(
        self, raw_scores: Any, story: dict[str, Any]
    ) -> list[dict[str, Any]]:
        if isinstance(raw_scores, dict):
            raw_by_name: dict[str, Any] = {}
            for name, value in raw_scores.items():
                criterion_name = clean_whitespace(str(name or "")).lower().replace(" ", "_")
                if not criterion_name:
                    continue
                if isinstance(value, dict):
                    raw_by_name[criterion_name] = {
                        "criterion": criterion_name,
                        "score": value.get("score"),
                        "reason": value.get("reason"),
                    }
                else:
                    raw_by_name[criterion_name] = {
                        "criterion": criterion_name,
                        "score": value,
                        "reason": "",
                    }
        elif isinstance(raw_scores, list):
            raw_by_name = {}
            for entry in raw_scores:
                if not isinstance(entry, dict):
                    continue
                criterion_name = (
                    clean_whitespace(str(entry.get("criterion") or "")).lower().replace(" ", "_")
                )
                if criterion_name:
                    raw_by_name[criterion_name] = entry
        else:
            return self._heuristic_criteria_scores(story)

        normalized = []
        heuristic = {e["criterion"]: e for e in self._heuristic_criteria_scores(story)}
        for criterion in self.criteria:
            entry = raw_by_name.get(criterion.name)
            if entry is None:
                normalized.append(heuristic[criterion.name])
                continue
            normalized.append(
                {
                    "criterion": criterion.name,
                    "material": criterion.material,
                    "score": _bounded_score_10(
                        entry.get("score"), default=heuristic[criterion.name]["score"]
                    ),
                    "reason": clean_whitespace(
                        str(entry.get("reason") or heuristic[criterion.name]["reason"])
                    ),
                }
            )
        return normalized

    def _heuristic_criteria_scores(self, story: dict[str, Any]) -> list[dict[str, Any]]:
        text = f"{story.get('title', '')} {story.get('body', '')}".lower()
        results = []
        for criterion in self.criteria:
            score = _heuristic_score_for_criterion(text, criterion.name, criterion.material)
            if criterion.name == "virality" and story.get("metrics"):
                metric_values = [
                    v
                    for v in story.get("metrics", {}).values()
                    if isinstance(v, (int, float))
                ]
                if metric_values and max(metric_values) > 0:
                    score = max(score, 6)
            results.append(
                {
                    "criterion": criterion.name,
                    "material": criterion.material,
                    "score": score,
                    "reason": f"Heuristic fallback for {criterion.name}.",
                }
            )
        return results

    def _fallback_review(self, story: dict[str, Any], *, reason: str) -> dict[str, Any]:
        raw = {
            "story_id": story.get("id"),
            "review_method": "heuristic_fallback",
            "criteria_scores": self._heuristic_criteria_scores(story),
            "topic_hint": story.get("title", "Untitled topic"),
            "summary": _fallback_summary(story),
            "claims_to_verify": [],
            "review_reason": (
                f"Heuristic fallback was used because the model could not return structured data: {reason}"
            ),
            "confidence": "low",
        }
        return self._normalize_review(raw, story)

    # ── Debug helpers ─────────────────────────────────────────────────────────

    def _write_debug_response(self, story_id: str, label: str, text: str) -> Path:
        timestamp = datetime.now().strftime("%Y-%m-%d_%H-%M-%S")
        outdir = self.settings.debug_dir / "ollama"
        outdir.mkdir(parents=True, exist_ok=True)
        path = outdir / f"{label}_{story_id}_{timestamp}.txt"
        path.write_text(text, encoding="utf-8")
        return path

    def _write_debug_event(self, story_id: str, label: str, payload: dict[str, Any]) -> Path:
        timestamp = datetime.now().strftime("%Y-%m-%d_%H-%M-%S")
        outdir = self.settings.debug_dir / "ollama"
        outdir.mkdir(parents=True, exist_ok=True)
        path = outdir / f"{label}_{story_id}_{timestamp}.json"
        path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
        return path


# ── Module-level helpers ───────────────────────────────────────────────────────


def _criterion_tokens(material: str, name: str) -> list[str]:
    defaults = {
        "israel_political_relevance": [
            "israel", "israeli", "jerusalem", "gaza", "west bank",
            "idf", "hezbollah", "hamas", "iran",
        ],
        "antisemitic_content": [
            "antisemit", "anti-semit", "jew-hating", "jewish control",
            "zionist plot", "exterminate", "genocide of jews",
        ],
        "anti_zionist_content": ["zionis", "anti-israel", "anti israel", "apartheid"],
        "misinformation_risk": ["false", "fake", "claim", "alleg", "reportedly", "unverified"],
        "virality": ["viral", "trending", "widely shared", "share"],
    }
    return defaults.get(name, [token for token in material.split() if len(token) > 4])


def _heuristic_score_for_criterion(text: str, criterion_name: str, material: str) -> int:
    tokens = _criterion_tokens(material.lower(), criterion_name)
    hits = sum(1 for token in tokens if token in text)

    if criterion_name == "israel_political_relevance":
        strong_hits = sum(
            1
            for token in (
                "hezbollah", "hamas", "netanyahu", "iran", "gaza", "west bank",
                "idf", "ceasefire", "hostage", "strike", "lebanon", "syria",
                "diplom", "cabinet", "government", "military", "talks",
            )
            if token in text
        )
        generic_hits = sum(1 for token in ("israel", "israeli", "jerusalem") if token in text)
        if strong_hits >= 2:
            return 8
        if strong_hits == 1:
            return 6
        if generic_hits >= 1:
            return 4
        return 1

    if hits >= 2:
        return 6
    if hits == 1:
        return 4
    return 1


def _story_prompt_block(story: dict[str, Any], max_body_chars: int) -> dict[str, Any]:
    return {
        "story_id": story.get("id"),
        "source": story.get("source"),
        "source_language": story.get("source_language"),
        "source_orientation": story.get("source_orientation"),
        "source_priority": story.get("source_priority"),
        "title": story.get("title"),
        "url": story.get("url"),
        "published_at": story.get("published_at"),
        "collected_at": story.get("collected_at"),
        "metrics": story.get("metrics", {}),
        "body": (story.get("body") or "")[:max_body_chars],
    }


def _criteria_prompt(criteria: list[ReviewCriterion]) -> str:
    return "\n".join(f"- {c.name}: {c.question}" for c in criteria)


def _json_from_text(text: str) -> dict[str, Any]:
    stripped = text.strip()
    if stripped.startswith("```"):
        stripped = re.sub(r"^```(?:json)?\s*", "", stripped)
        stripped = re.sub(r"\s*```$", "", stripped)

    try:
        return json.loads(stripped)
    except json.JSONDecodeError:
        decoder = json.JSONDecoder()
        start = stripped.find("{")
        while start != -1:
            try:
                obj, _ = decoder.raw_decode(stripped[start:])
                if isinstance(obj, dict):
                    return obj
            except json.JSONDecodeError:
                start = stripped.find("{", start + 1)
                continue
        raise


def _bounded_score_10(value: Any, default: int = 1) -> int:
    try:
        return max(1, min(10, int(value)))
    except (TypeError, ValueError):
        return default


def _as_list(value: Any) -> list[str]:
    if isinstance(value, list):
        return [clean_whitespace(str(item)) for item in value if clean_whitespace(str(item))]
    if isinstance(value, str) and clean_whitespace(value):
        return [clean_whitespace(value)]
    return []


def _normalize_topic_hint(value: Any, story: dict[str, Any]) -> str:
    topic_hint = clean_whitespace(str(value or ""))
    if topic_hint:
        return topic_hint[:80]
    title = clean_whitespace(str(story.get("title") or "Untitled topic"))
    words = title.split()
    return " ".join(words[:6]) or "Untitled topic"


def _normalize_summary(value: Any, story: dict[str, Any]) -> str:
    summary = clean_whitespace(str(value or ""))
    if summary:
        return summary[:280]
    return _fallback_summary(story)


def _mentions_from_text(story: dict[str, Any]) -> list[str]:
    text = f"{story.get('title', '')} {story.get('body', '')}"
    matches = re.findall(r"\b[A-Z][A-Za-z0-9''-]{2,}\b", text)
    blocked = {
        "The", "This", "That", "Once", "Daily", "Edition", "Registering", "Already",
        "Enter", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday",
        "Monday", "World", "News", "Update", "Updates", "Latest", "Breaking",
    }
    seen = []
    for match in matches:
        if match in blocked:
            continue
        if match not in seen:
            seen.append(match)
        if len(seen) >= 12:
            break
    return seen


def _fallback_summary(story: dict[str, Any]) -> str:
    body = clean_whitespace(str(story.get("body") or ""))
    if body:
        first_sentence = re.split(r"(?<=[.!?])\s+", body, maxsplit=1)[0]
        return clean_whitespace(first_sentence)[:280]
    return clean_whitespace(str(story.get("title") or ""))
