from __future__ import annotations

import json
import logging
import os
import re
import time
from datetime import datetime, timezone
from typing import Any

import requests
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry

from utils import clean_whitespace

log = logging.getLogger(__name__)

OLLAMA_URL = os.getenv("OLLAMA_URL", "http://localhost:11434/api/chat")
MODEL_NAME = os.getenv("OLLAMA_MODEL", "gemma4:31b")
REQUEST_TIMEOUT_SECONDS = int(os.getenv("OLLAMA_TIMEOUT_SECONDS", "900"))
MAX_BODY_CHARS = int(os.getenv("NEWS_MAX_BODY_CHARS", "2400"))
NUM_PREDICT = int(os.getenv("OLLAMA_NUM_PREDICT", "700"))
NUM_CTX = int(os.getenv("OLLAMA_NUM_CTX", "4096"))
STREAM_CHAT = os.getenv("OLLAMA_STREAM", "1") == "1"
PROGRESS_LOG_SECONDS = int(os.getenv("OLLAMA_PROGRESS_SECONDS", "15"))

_RETRY = Retry(
    total=2,
    connect=2,
    read=0,
    backoff_factor=1,
    status_forcelist=[429, 500, 502, 503, 504],
    allowed_methods=["POST"],
    raise_on_status=False,
)


def _make_session() -> requests.Session:
    session = requests.Session()
    adapter = HTTPAdapter(max_retries=_RETRY)
    session.mount("http://", adapter)
    session.mount("https://", adapter)
    return session


_SESSION = _make_session()


def _utc_now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def _call_ollama(
    messages: list[dict[str, str]],
    *,
    num_predict: int = NUM_PREDICT,
    json_format: bool = False,
) -> str:
    payload = {
        "model": MODEL_NAME,
        "messages": messages,
        "options": {
            "num_predict": num_predict,
            "num_ctx": NUM_CTX,
            "temperature": 0.1,
        },
    }
    if json_format:
        payload["format"] = "json"

    try:
        if STREAM_CHAT:
            return _stream_chat(payload)
        return _non_stream_chat(payload)
    except requests.exceptions.ReadTimeout as exc:
        raise RuntimeError(
            "Ollama timed out. Try lower NEWS_MAX_BODY_CHARS or OLLAMA_NUM_PREDICT, "
            "or increase OLLAMA_TIMEOUT_SECONDS."
        ) from exc
    except requests.exceptions.ConnectionError as exc:
        raise RuntimeError(
            "Could not connect to Ollama. Make sure Ollama is running and OLLAMA_URL is correct."
        ) from exc


def _stream_chat(payload: dict[str, Any]) -> str:
    chunks: list[str] = []
    chars = 0
    last_progress = time.monotonic()
    first_token_logged = False

    with _SESSION.post(
        OLLAMA_URL,
        json={**payload, "stream": True},
        timeout=(10, REQUEST_TIMEOUT_SECONDS),
        stream=True,
    ) as response:
        response.raise_for_status()

        for raw_line in response.iter_lines(decode_unicode=True):
            if not raw_line:
                continue
            try:
                data = json.loads(raw_line)
            except json.JSONDecodeError:
                continue

            if data.get("error"):
                raise RuntimeError(f"Ollama error: {data['error']}")

            token = data.get("message", {}).get("content", "")
            if token:
                chunks.append(token)
                chars += len(token)
                if not first_token_logged:
                    log.info("First tokens received from Ollama.")
                    first_token_logged = True

            now = time.monotonic()
            if now - last_progress >= PROGRESS_LOG_SECONDS:
                log.info("Ollama progress: %d characters generated...", chars)
                last_progress = now

            if data.get("done"):
                break

    return "".join(chunks).strip()


def _non_stream_chat(payload: dict[str, Any]) -> str:
    response = _SESSION.post(
        OLLAMA_URL,
        json={**payload, "stream": False},
        timeout=(10, REQUEST_TIMEOUT_SECONDS),
    )
    response.raise_for_status()
    data = response.json()
    if "message" not in data or "content" not in data["message"]:
        raise RuntimeError(f"Unexpected Ollama response format: {data}")
    return data["message"]["content"].strip()


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


def _bounded_int(value: Any, default: int = 0) -> int:
    try:
        return max(0, min(5, int(value)))
    except (TypeError, ValueError):
        return default


def _as_list(value: Any) -> list[str]:
    if isinstance(value, list):
        return [clean_whitespace(str(item)) for item in value if clean_whitespace(str(item))]
    if isinstance(value, str) and clean_whitespace(value):
        return [clean_whitespace(value)]
    return []


def _normalize_review(raw: dict[str, Any], story: dict[str, Any]) -> dict[str, Any]:
    scores = raw.get("scores") if isinstance(raw.get("scores"), dict) else {}
    normalized_scores = {
        "israel_political_relevance": _bounded_int(scores.get("israel_political_relevance")),
        "anti_zionist_content": _bounded_int(scores.get("anti_zionist_content")),
        "misinformation_risk": _bounded_int(scores.get("misinformation_risk")),
        "virality": _bounded_int(scores.get("virality")),
    }

    worth_reviewing = raw.get("worth_reviewing")
    if not isinstance(worth_reviewing, bool):
        worth_reviewing = (
            normalized_scores["israel_political_relevance"] >= 3
            or normalized_scores["misinformation_risk"] >= 3
            or normalized_scores["virality"] >= 4
        )

    priority = clean_whitespace(str(raw.get("priority", ""))).lower()
    if priority not in {"breaking", "high", "medium", "low", "ignore"}:
        score_total = (
            normalized_scores["israel_political_relevance"]
            + normalized_scores["misinformation_risk"]
            + normalized_scores["virality"]
        )
        if not worth_reviewing:
            priority = "ignore"
        elif score_total >= 11:
            priority = "high"
        elif score_total >= 7:
            priority = "medium"
        else:
            priority = "low"

    return {
        "story_id": story["id"],
        "reviewed_at": _utc_now(),
        "model": MODEL_NAME,
        "worth_reviewing": worth_reviewing,
        "priority": priority,
        "scores": normalized_scores,
        "source_language": clean_whitespace(str(raw.get("source_language") or story.get("source_language") or "unknown")),
        "political_orientation": clean_whitespace(
            str(raw.get("political_orientation") or story.get("source_orientation") or "unknown")
        ),
        "mentions": _as_list(raw.get("mentions"))[:20],
        "topic_hint": clean_whitespace(str(raw.get("topic_hint") or story.get("title") or "Untitled topic")),
        "one_sentence_summary": clean_whitespace(str(raw.get("one_sentence_summary") or story.get("title") or "")),
        "summary_bullets": _as_list(raw.get("summary_bullets"))[:6],
        "claims_to_verify": _as_list(raw.get("claims_to_verify"))[:8],
        "misinformation_notes": _as_list(raw.get("misinformation_notes"))[:6],
        "review_reason": clean_whitespace(str(raw.get("review_reason") or "")),
        "confidence": clean_whitespace(str(raw.get("confidence") or "medium")).lower(),
    }


def _fallback_review(story: dict[str, Any], *, reason: str) -> dict[str, Any]:
    text = f"{story.get('title', '')} {story.get('body', '')}".lower()
    keywords = [
        "israel",
        "israeli",
        "jerusalem",
        "zionis",
        "gaza",
        "hamas",
        "hezbollah",
        "iran",
        "idf",
        "hostage",
        "antisemit",
        "west bank",
    ]
    matches = sorted({keyword for keyword in keywords if keyword in text})
    relevance = 4 if matches else 1
    misinformation = 2 if matches else 1
    raw = {
        "worth_reviewing": bool(matches),
        "priority": "medium" if matches else "ignore",
        "scores": {
            "israel_political_relevance": relevance,
            "anti_zionist_content": 0,
            "misinformation_risk": misinformation,
            "virality": 0,
        },
        "mentions": matches,
        "topic_hint": story.get("title", "Untitled topic"),
        "one_sentence_summary": story.get("title", ""),
        "summary_bullets": [story.get("title", "")],
        "claims_to_verify": [],
        "misinformation_notes": [f"Fallback heuristic used: {reason}"],
        "review_reason": "Keyword fallback was used because the model response could not be parsed.",
        "confidence": "low",
    }
    return _normalize_review(raw, story)


def build_review_prompt(story: dict[str, Any]) -> str:
    metrics = story.get("metrics") or {}
    body = (story.get("body") or "")[:MAX_BODY_CHARS]
    return f"""
Review this media article for a source-faithful public diplomacy monitoring workflow.

Important rules:
- Use only the provided article text and metadata.
- Do not decide that a claim is false unless the article itself proves that.
- Treat misinformation_risk as "needs verification", not as a final truth verdict.
- Classify anti-Zionist content only when it is explicit in the article or quoted claims.
- If virality metrics are unavailable, score virality as 0 or 1 and mention that limitation.
- Return JSON only. No markdown.

Scoring:
- 0 means absent or unknown.
- 5 means very strong, urgent, or highly visible.

JSON schema:
{{
  "worth_reviewing": true,
  "priority": "breaking | high | medium | low | ignore",
  "scores": {{
    "israel_political_relevance": 0,
    "anti_zionist_content": 0,
    "misinformation_risk": 0,
    "virality": 0
  }},
  "source_language": "language name or unknown",
  "political_orientation": "source orientation if known, otherwise unknown",
  "mentions": ["people, places, groups, hashtags, or organizations"],
  "topic_hint": "short reusable topic name",
  "one_sentence_summary": "neutral one sentence summary",
  "summary_bullets": ["3-5 concise factual bullets"],
  "claims_to_verify": ["specific claims that should be checked against other sources"],
  "misinformation_notes": ["why verification may be needed, or empty list"],
  "review_reason": "why this is or is not worth review",
  "confidence": "high | medium | low"
}}

Article metadata:
Source: {story.get("source", "unknown")}
Source language: {story.get("source_language", "unknown")}
Source political orientation: {story.get("source_orientation", "unknown")}
Source priority: {story.get("source_priority", "unknown")}
Title: {story.get("title", "")}
URL: {story.get("url", "")}
Published at: {story.get("published_at") or "unknown"}
Collected at: {story.get("collected_at") or "unknown"}
Virality metrics: {json.dumps(metrics, ensure_ascii=False)}

Article body:
{body}
""".strip()


def review_story(story: dict[str, Any]) -> dict[str, Any]:
    log.info("Reviewing story with %s: %s", MODEL_NAME, story.get("title", "untitled"))
    messages = [
        {
            "role": "system",
            "content": (
                "You are a careful article triage analyst. You classify relevance, summarize "
                "faithfully, and never invent facts."
            ),
        },
        {"role": "user", "content": build_review_prompt(story)},
    ]
    raw_text = _call_ollama(messages, json_format=True)
    try:
        raw_json = _json_from_text(raw_text)
    except json.JSONDecodeError as exc:
        log.warning("Could not parse model JSON for %s: %s", story.get("id"), exc)
        return _fallback_review(story, reason="model returned invalid JSON")
    return _normalize_review(raw_json, story)


def build_report_prompt(reviewed_items: list[dict[str, Any]]) -> str:
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
                "source_language": review.get("source_language"),
                "political_orientation": review.get("political_orientation"),
                "priority": review.get("priority"),
                "scores": review.get("scores"),
                "topic": topic.get("name") or review.get("topic_hint"),
                "topic_status": item.get("topic_status"),
                "summary": review.get("one_sentence_summary"),
                "bullets": review.get("summary_bullets", [])[:4],
                "claims_to_verify": review.get("claims_to_verify", [])[:4],
                "mentions": review.get("mentions", [])[:10],
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

Output format:

Executive Summary
- 3 to 6 bullets

Priority Topics
Topic: <name>
Status: <new or existing>
Why it matters: <one sentence>
Source picture: <single-source or multi-source, include source names>
Key points:
- ...
Claims to verify:
- ...
Links:
- Source - Title - URL

Lower Priority / Excluded
- Short bullets for items not worth review, if any

Reviewed data:
{json.dumps(compact_items, ensure_ascii=False, indent=2)}
""".strip()


def synthesize_report(reviewed_items: list[dict[str, Any]]) -> str:
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
        {"role": "user", "content": build_report_prompt(reviewed_items)},
    ]
    return _call_ollama(messages, num_predict=max(NUM_PREDICT, 900))


def render_report_from_reviews(reviewed_items: list[dict[str, Any]]) -> str:
    if not reviewed_items:
        return "No articles were reviewed in this run."

    priority_order = {"breaking": 0, "high": 1, "medium": 2, "low": 3, "ignore": 4}
    sorted_items = sorted(
        reviewed_items,
        key=lambda item: priority_order.get(item.get("review", {}).get("priority", "ignore"), 5),
    )

    lines = [
        "Executive Summary",
        "- Automated synthesis was not available, so this report was rendered from structured Gemma reviews.",
    ]

    worth_count = sum(1 for item in reviewed_items if item.get("review", {}).get("worth_reviewing"))
    lines.append(f"- {worth_count} of {len(reviewed_items)} reviewed articles were marked worth human review.")
    lines.append("")
    lines.append("Priority Topics")

    for item in sorted_items:
        story = item["story"]
        review = item["review"]
        if not review.get("worth_reviewing"):
            continue

        topic = item.get("topic", {})
        cross_check = item.get("cross_check", {})
        sources = sorted(set((topic.get("sources") or []) + [story.get("source", "unknown")]))
        lines.extend(
            [
                f"Topic: {topic.get('name') or review.get('topic_hint')}",
                f"Status: {item.get('topic_status', 'unknown')}",
                f"Why it matters: {review.get('review_reason') or review.get('one_sentence_summary')}",
                (
                    "Source picture: "
                    f"{'multi-source' if cross_check.get('other_source_count', 0) > 0 else 'single-source'}; "
                    f"{', '.join(sources)}"
                ),
                "Key points:",
            ]
        )
        for bullet in review.get("summary_bullets", [])[:4] or [review.get("one_sentence_summary", "")]:
            lines.append(f"- {bullet}")
        lines.append("Claims to verify:")
        claims = review.get("claims_to_verify", [])[:4]
        if claims:
            for claim in claims:
                lines.append(f"- {claim}")
        else:
            lines.append("- None listed by the model.")
        lines.append("Links:")
        lines.append(f"- {story.get('source', 'unknown')} - {story.get('title', '')} - {story.get('url', '')}")
        lines.append("")

    excluded = [item for item in sorted_items if not item.get("review", {}).get("worth_reviewing")]
    if excluded:
        lines.append("Lower Priority / Excluded")
        for item in excluded:
            story = item["story"]
            review = item["review"]
            lines.append(f"- {story.get('source', 'unknown')}: {story.get('title', '')} - {review.get('review_reason', '')}")

    return "\n".join(lines).strip()
