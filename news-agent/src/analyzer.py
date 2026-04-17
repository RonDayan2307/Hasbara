from __future__ import annotations

import json
import logging
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from contracts import CriterionScore, ReviewResult, ReviewedItem, RunManifest, Story
from criteria import ReviewCriterion, load_review_criteria
from criterion_skill import CriterionSkill, load_skills
from ollama_client import OllamaClient
from report_renderer import render_report_from_reviews  # re-exported for backward compat
from review_cache import ReviewCache
from settings import RuntimeSettings
from utils import clean_whitespace, shorten_for_display, stable_id

log = logging.getLogger(__name__)

PROMPT_VERSION = "stage1-review-v6"
NORMALIZATION_VERSION = "stage1-normalize-v5"


def _utc_now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


class LocalAiAnalyzer:
    def __init__(self, settings: RuntimeSettings) -> None:
        self.settings = settings
        self.criteria = load_review_criteria(settings.criteria_path)
        self.skills: list[CriterionSkill] = load_skills(self.criteria)
        self.criteria_code_map, self.criteria_code_lookup = _criterion_code_maps(self.criteria)
        self.prompt_version = PROMPT_VERSION
        self.normalization_version = NORMALIZATION_VERSION
        self.cache_namespace = _build_cache_namespace(
            settings.local_ai_model,
            self.criteria,
            self.prompt_version,
            self.normalization_version,
        )
        self.client = OllamaClient(settings)
        self.cache = ReviewCache.load(settings.review_cache_path, self.cache_namespace)

    def health_check(self) -> bool:
        return self.client.health_check()

    def review_story(self, story: Story) -> ReviewResult:
        story_id = story.get("id", "")
        if story_id:
            cached = self.cache.get(story_id)
            if cached is not None:
                log.info(
                    "Cache hit - skipping Ollama for: %s",
                    shorten_for_display(story.get("title", story_id), max_length=88),
                )
                return self._normalize_review(cached, story)

        review = self._review_single_story_with_fallback(story, reason="sequential review")
        if story_id and review.get("review_method") == "model":
            self.cache.put(story_id, review)
        return review

    def synthesize_report(
        self,
        reviewed_items: list[ReviewedItem],
        run_manifest: RunManifest,
    ) -> str:
        if self.settings.report_mode != "model":
            return render_report_from_reviews(
                reviewed_items,
                self.criteria,
                run_manifest=run_manifest,
                model_name=self.settings.local_ai_model,
            )

        if not reviewed_items:
            return "No articles were reviewed in this run."

        messages = [
            {
                "role": "system",
                "content": (
                    "You write concise, neutral monitoring reports from structured article reviews. "
                    "You preserve uncertainty, mention single-source limitations, and never add facts."
                ),
            },
            {"role": "user", "content": self._build_report_prompt(reviewed_items, run_manifest)},
        ]
        return self.client.chat(messages, num_predict=self.settings.report_num_predict)

    def _review_single_story_with_fallback(self, story: Story, *, reason: str) -> ReviewResult:
        log.info(
            "Reviewing: %s",
            shorten_for_display(story.get("title", story.get("id", "untitled")), max_length=88),
        )
        review, failure_reason = self._review_single_story(story)
        if review is not None:
            return review

        log.warning(
            "Falling back to heuristic for %s: %s",
            shorten_for_display(story.get("title", story.get("id", "untitled")), max_length=68),
            failure_reason or reason,
        )
        return self._fallback_review(story, reason=failure_reason or reason)

    def _review_single_story(self, story: Story) -> tuple[ReviewResult | None, str]:
        prompt = self._build_single_review_prompt(story)
        messages = [
            {
                "role": "system",
                "content": (
                    "You are a public diplomacy monitoring analyst reviewing media articles about Israel "
                    "and the Middle East. You score articles on specific criteria using the full 1-10 scale. "
                    "You return strict JSON only and never invent facts. "
                    "Consider the source's stated orientation when scoring — state-controlled or "
                    "known-propaganda outlets warrant higher misinformation_risk scores."
                ),
            },
            {"role": "user", "content": prompt},
        ]
        try:
            raw_text = self.client.chat(
                messages,
                num_predict=max(120, min(self.settings.review_num_predict_per_story, 512)),
                json_format=True,
            )
        except Exception as exc:
            log.warning("Single-story review failed for %s: %s", story.get("id"), exc)
            self._write_debug_event(
                story.get("id", "untitled"),
                "single_request_failure",
                self._debug_payload(
                    stage="primary",
                    story=story,
                    prompt=prompt,
                    output_text="",
                    reason=str(exc),
                    parse_failure_class=exc.__class__.__name__,
                ),
            )
            return None, str(exc)

        if not raw_text.strip():
            log.info("Primary output was empty for %s; retrying with compact format.", story.get("id"))
            self._write_debug_event(
                story.get("id", "untitled"),
                "single_empty_response",
                self._debug_payload(
                    stage="primary",
                    story=story,
                    prompt=prompt,
                    output_text=raw_text,
                    reason="empty primary response",
                    parse_failure_class="empty_output",
                ),
            )
            return self._review_single_story_compact(story, reason="empty primary response")

        try:
            raw_json = _json_from_text(raw_text)
            parse_mode = "json"
        except json.JSONDecodeError as exc:
            salvaged = _salvage_review_payload(raw_text, story, self.criteria_code_lookup)
            if salvaged is not None:
                log.info("Recovered malformed primary output for %s using tolerant parsing.", story.get("id"))
                return self._normalize_review(self._extract_single_review(salvaged), story), ""
            debug_path = self._write_debug_response(
                story.get("id", "untitled"),
                "single_invalid_json",
                raw_text,
            )
            self._write_debug_event(
                story.get("id", "untitled"),
                "single_invalid_json_context",
                self._debug_payload(
                    stage="primary",
                    story=story,
                    prompt=prompt,
                    output_text=raw_text,
                    reason=str(exc),
                    parse_failure_class="json_decode_error",
                    raw_output_path=str(debug_path),
                ),
            )
            log.info(
                "Primary output for %s was malformed (%s); retrying with compact format. Saved to %s",
                story.get("id"),
                exc,
                debug_path,
            )
            return self._review_single_story_compact(story, reason="invalid primary JSON")

        if parse_mode != "json":
            log.info("Recovered primary output for %s using %s.", story.get("id"), parse_mode)
        return self._normalize_review(self._extract_single_review(raw_json), story), ""

    def _review_single_story_compact(
        self,
        story: Story,
        *,
        reason: str,
    ) -> tuple[ReviewResult | None, str]:
        prompt = self._build_compact_single_review_prompt(story)
        messages = [
            {
                "role": "system",
                "content": (
                    "You are a public diplomacy monitoring analyst. Return only the requested compact lines. "
                    "Score each criterion independently using the full 1-10 range. "
                    "Do not use JSON, markdown, bullets, or explanatory text."
                ),
            },
            {"role": "user", "content": prompt},
        ]
        try:
            raw_text = self.client.chat(
                messages,
                num_predict=max(80, min(self.settings.review_num_predict_per_story // 2, 256)),
                json_format=False,
            )
        except Exception as exc:
            log.warning("Compact retry failed for %s: %s", story.get("id"), exc)
            self._write_debug_event(
                story.get("id", "untitled"),
                "compact_request_failure",
                self._debug_payload(
                    stage="compact_retry",
                    story=story,
                    prompt=prompt,
                    output_text="",
                    reason=str(exc),
                    parse_failure_class=exc.__class__.__name__,
                ),
            )
            return None, f"{reason}; compact retry failed: {exc}"

        if not raw_text.strip():
            log.info("Compact retry was empty for %s; requesting corrected output.", story.get("id"))
            self._write_debug_event(
                story.get("id", "untitled"),
                "compact_empty_response",
                self._debug_payload(
                    stage="compact_retry",
                    story=story,
                    prompt=prompt,
                    output_text=raw_text,
                    reason=reason,
                    parse_failure_class="empty_output",
                ),
            )
            return self._repair_single_story_output(
                story,
                bad_output=raw_text,
                prior_reason=f"{reason}; compact retry returned empty output",
                failure_stage="compact_retry",
            )

        parsed_compact = _parse_compact_review_payload(raw_text, story, self.criteria_code_lookup)
        if parsed_compact is not None:
            return self._normalize_review(self._extract_single_review(parsed_compact), story), ""

        try:
            raw_json = _json_from_text(raw_text)
        except json.JSONDecodeError as exc:
            salvaged = _salvage_review_payload(raw_text, story, self.criteria_code_lookup)
            if salvaged is not None:
                log.info("Recovered malformed compact output for %s using tolerant parsing.", story.get("id"))
                return self._normalize_review(self._extract_single_review(salvaged), story), ""
            debug_path = self._write_debug_response(
                story.get("id", "untitled"),
                "compact_invalid_json",
                raw_text,
            )
            self._write_debug_event(
                story.get("id", "untitled"),
                "compact_invalid_json_context",
                self._debug_payload(
                    stage="compact_retry",
                    story=story,
                    prompt=prompt,
                    output_text=raw_text,
                    reason=str(exc),
                    parse_failure_class="json_decode_error",
                    raw_output_path=str(debug_path),
                ),
            )
            log.info(
                "Compact retry for %s was malformed (%s); requesting corrected output. Saved to %s",
                story.get("id"),
                exc,
                debug_path,
            )
            return self._repair_single_story_output(
                story,
                bad_output=raw_text,
                prior_reason=f"{reason}; compact retry returned invalid output",
                failure_stage="compact_retry",
            )

        return self._normalize_review(self._extract_single_review(raw_json), story), ""

    def _repair_single_story_output(
        self,
        story: Story,
        *,
        bad_output: str,
        prior_reason: str,
        failure_stage: str,
    ) -> tuple[ReviewResult | None, str]:
        prompt = self._build_repair_review_prompt(story, bad_output, prior_reason)
        messages = [
            {
                "role": "system",
                "content": (
                    "You are fixing your own malformed answer. "
                    "Return only the corrected compact lines in the requested format."
                ),
            },
            {"role": "user", "content": prompt},
        ]
        try:
            raw_text = self.client.chat(
                messages,
                num_predict=max(60, min(100, self.settings.review_num_predict_per_story)),
                json_format=False,
            )
        except Exception as exc:
            log.warning("Repair retry failed for %s: %s", story.get("id"), exc)
            self._write_debug_event(
                story.get("id", "untitled"),
                "repair_request_failure",
                self._debug_payload(
                    stage="repair_retry",
                    story=story,
                    prompt=prompt,
                    output_text="",
                    reason=str(exc),
                    parse_failure_class=exc.__class__.__name__,
                ),
            )
            return None, f"{prior_reason}; repair retry failed: {exc}"

        if not raw_text.strip():
            self._write_debug_event(
                story.get("id", "untitled"),
                "repair_empty_response",
                self._debug_payload(
                    stage="repair_retry",
                    story=story,
                    prompt=prompt,
                    output_text=raw_text,
                    reason=prior_reason,
                    parse_failure_class="empty_output",
                ),
            )
            return None, f"{prior_reason}; repair retry returned empty output"

        parsed = _parse_compact_review_payload(raw_text, story, self.criteria_code_lookup)
        if parsed is not None:
            log.info("Recovered review for %s after repair retry.", story.get("id"))
            return self._normalize_review(self._extract_single_review(parsed), story), ""

        try:
            raw_json = _json_from_text(raw_text)
            return self._normalize_review(self._extract_single_review(raw_json), story), ""
        except json.JSONDecodeError as exc:
            debug_path = self._write_debug_response(
                story.get("id", "untitled"),
                "repair_invalid_output",
                raw_text,
            )
            self._write_debug_event(
                story.get("id", "untitled"),
                "repair_invalid_output_context",
                self._debug_payload(
                    stage="repair_retry",
                    story=story,
                    prompt=prompt,
                    output_text=raw_text,
                    reason=f"{prior_reason}; {exc}",
                    parse_failure_class="json_decode_error",
                    raw_output_path=str(debug_path),
                ),
            )
            log.warning(
                "Could not parse repair retry output for %s after %s. Saved to %s",
                story.get("id"),
                failure_stage,
                debug_path,
            )
            return None, f"{prior_reason}; repair retry returned invalid output"

    def _build_single_review_prompt(self, story: Story) -> str:
        story_block = _story_prompt_block(story, self.settings.max_body_chars)
        criteria_block = _criteria_scoring_prompt(self.criteria)
        example_scores = _example_scores_json(self.criteria)
        return f"""
Score this article for an Israeli public diplomacy monitoring system.

Criteria (score each independently, 1-10):
{criteria_block}

Return exactly one JSON object with these keys:
- "story_id": the story id string
- "topic": 2-6 word topic label
- "summary": one neutral sentence, max 18 words
- "reason": why this matters or not, max 12 words
- "confidence": "high" | "medium" | "low"
- "claims": list of 0-1 short factual claims worth verifying
- "narrative_frame": one of "factual_reporting" | "opinion" | "advocacy" | "analysis" | "editorial"
- "scores": object mapping each criterion name to its integer score (1-10)

Example (scores will vary per article):
{{"story_id":"abc","topic":"Hezbollah border escalation","summary":"Rockets hit northern Israel as tensions rise.","reason":"Direct Israel security coverage","confidence":"high","claims":["3 rockets intercepted"],"narrative_frame":"factual_reporting","scores":{example_scores}}}

Rules:
- Score each criterion independently based on article content.
- Use the full 1-10 range. Most articles will have varied scores across criteria.
- Articles directly about Israel should score high on israel_political_relevance.
- If unsure about a criterion, prefer mid-range scores (4-6), not 1.
- Do not add extra keys or markdown.

Story:
{json.dumps(story_block, ensure_ascii=False, separators=(",", ":"))}
""".strip()

    def _build_compact_single_review_prompt(self, story: Story) -> str:
        story_block = _story_prompt_block(story, min(self.settings.max_body_chars, 500))
        criteria_block = _criteria_scoring_prompt(self.criteria)
        example_scores_line = _example_scores_compact(self.criteria)
        return f"""
Score this article for Israeli public diplomacy monitoring.

Criteria (score each 1-10):
{criteria_block}

Return exactly these lines and nothing else:
story_id=<story id>
topic=<2-6 word topic>
summary=<one neutral sentence, max 18 words>
reason=<why this matters, max 12 words>
confidence=<high|medium|low>
claims=<one short claim or empty>
narrative_frame=<factual_reporting|opinion|advocacy|analysis|editorial>
scores=<comma-separated name:score pairs>

Example scores line: scores={example_scores_line}

Rules:
- Score each criterion independently. Use the full 1-10 range.
- Articles about Israel should score high on israel_political_relevance.
- If unsure, prefer mid-range (4-6), not 1.

Story:
{json.dumps(story_block, ensure_ascii=False, separators=(",", ":"))}
""".strip()

    def _build_repair_review_prompt(self, story: Story, bad_output: str, reason: str) -> str:
        story_block = _story_prompt_block(story, min(self.settings.max_body_chars, 420))
        criteria_block = _criteria_scoring_prompt(self.criteria)
        example_scores_line = _example_scores_compact(self.criteria)
        bad_output_snippet = shorten_for_display(clean_whitespace(bad_output), max_length=320)
        return f"""
Your previous answer was malformed: {reason}

Score this article for Israeli public diplomacy monitoring.

Criteria (score each 1-10):
{criteria_block}

Return exactly these lines and nothing else:
story_id=<story id>
topic=<2-6 word topic>
summary=<one neutral sentence, max 18 words>
reason=<why this matters, max 12 words>
confidence=<high|medium|low>
claims=<one short claim or empty>
narrative_frame=<factual_reporting|opinion|advocacy|analysis|editorial>
scores=<comma-separated name:score pairs>

Example scores line: scores={example_scores_line}

Previous malformed output:
{bad_output_snippet or "<empty>"}

Story:
{json.dumps(story_block, ensure_ascii=False, separators=(",", ":"))}
""".strip()

    def _build_report_prompt(self, reviewed_items: list[ReviewedItem], run_manifest: RunManifest) -> str:
        compact_items = []
        for item in reviewed_items:
            story = item["story"]
            review = item["review"]
            topic = item.get("topic", {})
            compact_items.append(
                {
                    "source": story.get("source"),
                    "language": review.get("source_language"),
                    "orientation": review.get("political_orientation"),
                    "title": story.get("title"),
                    "url": story.get("url"),
                    "priority": review.get("priority"),
                    "review_method": review.get("review_method"),
                    "review_quality": review.get("review_quality"),
                    "topic": topic.get("name") or review.get("topic_hint"),
                    "topic_status": item.get("topic_status"),
                    "summary": review.get("summary"),
                    "criteria_scores": review.get("criteria_scores", []),
                    "claims_to_verify": review.get("claims_to_verify", [])[:4],
                }
            )

        prompt_payload = {
            "run_status": run_manifest.get("status"),
            "counts": run_manifest.get("counts", {}),
            "source_health": run_manifest.get("source_health", []),
            "items": compact_items,
        }
        return f"""
Create a concise source-faithful monitoring report from these reviewed articles.

Rules:
- Use only the reviewed data below.
- Group related items by topic.
- Call out degraded behavior or source failures.
- Make clear which topics are single-source and which are corroborated by multiple sources.
- Do not add recommendations or invented facts.

Reviewed data:
{json.dumps(prompt_payload, ensure_ascii=False, indent=2)}
""".strip()

    def _extract_single_review(self, raw_json: Any) -> dict[str, Any]:
        if isinstance(raw_json, dict):
            review = raw_json.get("review")
            if isinstance(review, dict):
                return _expand_review_payload(review)
            reviews = raw_json.get("reviews")
            if isinstance(reviews, list):
                for entry in reviews:
                    if isinstance(entry, dict):
                        return _expand_review_payload(entry)
            return _expand_review_payload(raw_json)
        if isinstance(raw_json, list):
            for entry in raw_json:
                if isinstance(entry, dict):
                    return _expand_review_payload(entry)
        return {}

    def _normalize_review(self, raw: dict[str, Any], story: Story) -> ReviewResult:
        review_method = clean_whitespace(str(raw.get("review_method") or "model")).lower()
        confidence = clean_whitespace(str(raw.get("confidence") or "medium")).lower()
        criteria_scores = self._normalize_criteria_scores(
            raw.get("scores") if raw.get("scores") is not None else raw.get("criteria_scores"),
            story,
            review_method=review_method,
        )
        score_values = [entry["score"] for entry in criteria_scores]
        max_score = max(score_values) if score_values else 1
        average_score = round(sum(score_values) / len(score_values), 1) if score_values else 1.0
        review_quality = _review_quality(review_method, confidence)
        worth_reviewing, priority = self._worth_and_priority(
            review_method=review_method,
            review_quality=review_quality,
            max_score=max_score,
            average_score=average_score,
        )
        review_reason = clean_whitespace(
            str(raw.get("why") or raw.get("review_reason") or _derived_review_reason(criteria_scores, worth_reviewing))
        )

        narrative_frame = clean_whitespace(str(raw.get("narrative_frame") or "unknown")).lower()
        valid_frames = {"factual_reporting", "opinion", "advocacy", "analysis", "editorial", "unknown"}
        if narrative_frame not in valid_frames:
            narrative_frame = "unknown"

        result: ReviewResult = {
            "story_id": story["id"],
            "reviewed_at": _utc_now(),
            "model": self.settings.local_ai_model,
            "review_method": review_method if review_method in {"model", "cached", "heuristic_fallback"} else "model",
            "review_quality": review_quality,
            "worth_reviewing": worth_reviewing,
            "priority": priority,
            "criteria_scores": criteria_scores,
            "score_summary": {"max_score": float(max_score), "average_score": float(average_score)},
            "source_language": clean_whitespace(str(story.get("source_language") or "unknown")),
            "political_orientation": clean_whitespace(str(story.get("source_orientation") or "unknown")),
            "mentions": _mentions_from_text(story),
            "topic_hint": _normalize_topic_hint(raw.get("topic") or raw.get("topic_hint"), story),
            "summary": _normalize_summary(raw.get("summary"), story),
            "claims_to_verify": _normalize_claims(raw.get("claims") or raw.get("claims_to_verify")),
            "review_reason": shorten_for_display(review_reason, max_length=220),
            "confidence": confidence if confidence in {"high", "medium", "low"} else "medium",
            "narrative_frame": narrative_frame,
            "prompt_version": self.prompt_version,
            "normalization_version": self.normalization_version,
            "cache_namespace": self.cache_namespace,
        }
        return result

    def _normalize_criteria_scores(
        self,
        raw_scores: Any,
        story: Story,
        *,
        review_method: str,
    ) -> list[CriterionScore]:
        if review_method == "heuristic_fallback" or raw_scores is None:
            return self._heuristic_criteria_scores(story)

        raw_by_name: dict[str, Any] = {}
        if isinstance(raw_scores, dict):
            for name, value in raw_scores.items():
                criterion_name = _normalize_criterion_key(name, self.criteria_code_lookup)
                if not criterion_name:
                    continue
                raw_by_name[criterion_name] = value
        elif isinstance(raw_scores, list):
            for entry in raw_scores:
                if not isinstance(entry, dict):
                    continue
                criterion_name = _normalize_criterion_key(
                    entry.get("criterion"),
                    self.criteria_code_lookup,
                )
                if criterion_name:
                    raw_by_name[criterion_name] = entry

        normalized: list[CriterionScore] = []
        for criterion in self.criteria:
            entry = raw_by_name.get(criterion.name)
            if entry is None:
                normalized.append(
                    {
                        "criterion": criterion.name,
                        "material": criterion.material,
                        "score": 1,
                        "reason": "Criterion missing from model response.",
                    }
                )
                continue

            if isinstance(entry, dict):
                score = _bounded_score_10(entry.get("score"), default=1)
                reason = clean_whitespace(str(entry.get("reason") or "Model score."))
            else:
                score = _bounded_score_10(entry, default=1)
                reason = "Model score."

            normalized.append(
                {
                    "criterion": criterion.name,
                    "material": criterion.material,
                    "score": score,
                    "reason": shorten_for_display(reason, max_length=120),
                }
            )
        return normalized

    def _heuristic_criteria_scores(self, story: Story) -> list[CriterionScore]:
        text = f"{story.get('title', '')} {story.get('body', '')}".lower()
        results: list[CriterionScore] = []
        for criterion in self.criteria:
            score = _heuristic_score_for_criterion(text, criterion.name, criterion.material)
            if criterion.name == "virality" and story.get("metrics"):
                metric_values = [
                    value
                    for value in story.get("metrics", {}).values()
                    if isinstance(value, (int, float))
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

    def _worth_and_priority(
        self,
        *,
        review_method: str,
        review_quality: str,
        max_score: int,
        average_score: float,
    ) -> tuple[bool, str]:
        if review_method == "heuristic_fallback":
            worthy = (
                max_score >= max(self.settings.priority_breaking_min_score, 9)
                and average_score >= max(self.settings.review_average_min_score + 1, 5)
            )
            return worthy, ("low" if worthy else "ignore")

        if review_quality == "low_confidence":
            worthy = (
                max_score >= self.settings.priority_high_min_score
                or average_score >= max(self.settings.review_average_min_score + 1, 7)
            )
        else:
            worthy = (
                max_score >= self.settings.review_worthy_min_score
                or average_score >= self.settings.review_average_min_score
            )

        if not worthy:
            return False, "ignore"
        if review_quality != "low_confidence" and max_score >= self.settings.priority_breaking_min_score:
            return True, "breaking"
        if max_score >= self.settings.priority_high_min_score:
            return True, "high"
        if review_quality == "low_confidence":
            return True, "low"
        return True, "medium"

    def _fallback_review(self, story: Story, *, reason: str) -> ReviewResult:
        raw = {
            "story_id": story.get("id"),
            "review_method": "heuristic_fallback",
            "topic": story.get("title", "Untitled topic"),
            "summary": _fallback_summary(story),
            "claims": [],
            "why": (
                f"Heuristic fallback was used because the model could not return structured data: {reason}"
            ),
            "confidence": "low",
            "scores": self._heuristic_score_map(story),
        }
        return self._normalize_review(raw, story)

    def _heuristic_score_map(self, story: Story) -> dict[str, int]:
        return {entry["criterion"]: entry["score"] for entry in self._heuristic_criteria_scores(story)}

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

    def _debug_payload(
        self,
        *,
        stage: str,
        story: Story,
        prompt: str,
        output_text: str,
        reason: str,
        parse_failure_class: str,
        raw_output_path: str | None = None,
    ) -> dict[str, Any]:
        payload = {
            "stage": stage,
            "story_id": story.get("id"),
            "title": story.get("title"),
            "url": story.get("url"),
            "reason": reason,
            "parse_failure_class": parse_failure_class,
            "prompt_length": len(prompt),
            "output_length": len(output_text or ""),
            "prompt_version": self.prompt_version,
            "normalization_version": self.normalization_version,
            "cache_namespace": self.cache_namespace,
        }
        if raw_output_path:
            payload["raw_output_path"] = raw_output_path
        return payload


def _build_cache_namespace(
    model_name: str,
    criteria: list[ReviewCriterion],
    prompt_version: str,
    normalization_version: str,
) -> str:
    criteria_signature = "|".join(f"{criterion.name}:{criterion.material}" for criterion in criteria)
    return stable_id(model_name, criteria_signature, prompt_version, normalization_version, length=24)


def _review_quality(review_method: str, confidence: str) -> str:
    if review_method == "heuristic_fallback":
        return "fallback"
    if confidence == "low":
        return "low_confidence"
    return "high_confidence"


def _derived_review_reason(criteria_scores: list[CriterionScore], worth_reviewing: bool) -> str:
    if not criteria_scores:
        return "No structured criteria scores were available."
    top = max(criteria_scores, key=lambda entry: entry.get("score", 0))
    if worth_reviewing:
        return f"Highest signal came from {top['criterion']} ({top['score']}/10)."
    return f"Insufficient signal for review; highest score was {top['criterion']} ({top['score']}/10)."


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


def _story_prompt_block(story: Story, max_body_chars: int) -> dict[str, Any]:
    block: dict[str, Any] = {
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
        "body": clean_whitespace(story.get("body", ""))[:max_body_chars],
    }
    subtitle = story.get("subtitle")
    if subtitle:
        block["subtitle"] = clean_whitespace(subtitle)
    return block


def _criterion_code_maps(criteria: list[ReviewCriterion]) -> tuple[dict[str, str], dict[str, str]]:
    name_to_code: dict[str, str] = {}
    code_to_name: dict[str, str] = {}
    for criterion in criteria:
        base = "".join(part[:1] for part in criterion.name.split("_") if part)[:4] or criterion.name[:3]
        code = base.lower()
        suffix = 2
        while code in code_to_name and code_to_name[code] != criterion.name:
            code = f"{base.lower()}{suffix}"
            suffix += 1
        name_to_code[criterion.name] = code
        code_to_name[code] = criterion.name
    return name_to_code, code_to_name


def _criteria_code_prompt(criteria: list[ReviewCriterion], code_map: dict[str, str]) -> str:
    """Legacy code-based prompt (kept for backward compat with cached reviews)."""
    return "\n".join(
        f"- {code_map[criterion.name]} = {criterion.name}"
        for criterion in criteria
    )


def _criteria_scoring_prompt(criteria: list[ReviewCriterion]) -> str:
    lines = []
    for criterion in criteria:
        lines.append(f"- {criterion.name} (1-10): {criterion.question}")
        lines.append(f"  Scale: {criterion.scale_description}")
    return "\n".join(lines)


def _example_scores_json(criteria: list[ReviewCriterion]) -> str:
    """Build a varied example scores object for JSON prompt to avoid anchoring bias."""
    example_values = [8, 1, 2, 3, 5, 7, 4, 6, 9, 1]
    parts = []
    for i, criterion in enumerate(criteria):
        score = example_values[i % len(example_values)]
        parts.append(f'"{criterion.name}":{score}')
    return "{" + ",".join(parts) + "}"


def _example_scores_compact(criteria: list[ReviewCriterion]) -> str:
    """Build a varied example scores line for compact prompt."""
    example_values = [8, 1, 2, 3, 5, 7, 4, 6, 9, 1]
    parts = []
    for i, criterion in enumerate(criteria):
        score = example_values[i % len(example_values)]
        parts.append(f"{criterion.name}:{score}")
    return ",".join(parts)


def _normalize_criterion_key(value: Any, code_lookup: dict[str, str]) -> str:
    key = clean_whitespace(str(value or "")).lower().replace(" ", "_")
    if not key:
        return ""
    return code_lookup.get(key, key)


def _expand_review_payload(raw: dict[str, Any]) -> dict[str, Any]:
    expanded = dict(raw)
    # Map old short keys to canonical names (backward compat)
    if "i" in expanded and "story_id" not in expanded:
        expanded["story_id"] = expanded.get("i")
    if "t" in expanded and "topic" not in expanded:
        expanded["topic"] = expanded.get("t")
    if "s" in expanded and "summary" not in expanded:
        expanded["summary"] = expanded.get("s")
    if "w" in expanded and "why" not in expanded:
        expanded["why"] = expanded.get("w")
    if "c" in expanded and "confidence" not in expanded:
        expanded["confidence"] = expanded.get("c")
    if "l" in expanded and "claims" not in expanded:
        expanded["claims"] = expanded.get("l")
    if "g" in expanded and "scores" not in expanded:
        expanded["scores"] = expanded.get("g")
    # Map new full key names (v6 prompts use "reason" instead of "why")
    if "reason" in expanded and "why" not in expanded:
        expanded["why"] = expanded.get("reason")
    if "nf" in expanded and "narrative_frame" not in expanded:
        expanded["narrative_frame"] = expanded.get("nf")
    return expanded


def _parse_compact_review_payload(
    text: str,
    story: Story,
    code_lookup: dict[str, str],
) -> dict[str, Any] | None:
    parsed = _parse_compact_line_payload(text, code_lookup)
    if parsed is not None:
        return parsed
    return _salvage_review_payload(text, story, code_lookup)


def _parse_compact_line_payload(text: str, code_lookup: dict[str, str]) -> dict[str, Any] | None:
    lines = [clean_whitespace(line) for line in text.splitlines() if clean_whitespace(line)]
    data: dict[str, Any] = {}
    # Map both old short keys and new full key names
    string_keys = {
        "i": "i", "story_id": "i",
        "t": "t", "topic": "t",
        "s": "s", "summary": "s",
        "w": "w", "reason": "w",
        "c": "c", "confidence": "c",
        "narrative_frame": "narrative_frame", "nf": "narrative_frame",
    }
    list_keys = {"l", "claims"}
    score_keys = {"g", "scores"}

    for line in lines:
        if "=" not in line:
            continue
        key, value = line.split("=", 1)
        key = clean_whitespace(key).lower().replace(" ", "_")
        value = clean_whitespace(value)

        if key in string_keys:
            data[string_keys[key]] = value
        elif key in list_keys:
            if not value or value in {"[]", "-", "none"}:
                data["l"] = []
            else:
                claims = [clean_whitespace(part) for part in value.split("||") if clean_whitespace(part)]
                data["l"] = claims[:2]
        elif key in score_keys:
            scores: dict[str, int] = {}
            for pair in value.split(","):
                if ":" not in pair:
                    continue
                score_key, score_value = pair.split(":", 1)
                criterion_name = _normalize_criterion_key(score_key, code_lookup)
                if criterion_name:
                    scores[criterion_name] = _bounded_score_10(score_value, default=1)
            data["g"] = scores

    if any(key in data for key in ("t", "s", "g")):
        return _expand_review_payload(data)
    return None


def _salvage_review_payload(
    text: str,
    story: Story,
    code_lookup: dict[str, str],
) -> dict[str, Any] | None:
    stripped = _strip_code_fences(text)
    payload: dict[str, Any] = {}
    string_fields = {
        "story_id": ["story_id", "i"],
        "topic": ["topic", "topic_hint", "t"],
        "summary": ["summary", "s"],
        "why": ["why", "reason", "review_reason", "w"],
        "confidence": ["confidence", "c"],
        "narrative_frame": ["narrative_frame", "nf"],
    }
    for target, keys in string_fields.items():
        value = _extract_string_field(stripped, keys)
        if value:
            payload[target] = value

    claims = _extract_claims_field(stripped, ["claims", "claims_to_verify", "l"])
    if claims:
        payload["claims"] = claims

    scores = _extract_scores_map(stripped, code_lookup)
    if scores:
        payload["scores"] = scores

    if payload:
        payload.setdefault("story_id", story.get("id"))
        return payload
    return None


def _strip_code_fences(text: str) -> str:
    stripped = text.strip()
    if stripped.startswith("```"):
        stripped = re.sub(r"^```(?:json)?\s*", "", stripped)
        stripped = re.sub(r"\s*```$", "", stripped)
    return stripped.strip()


def _extract_string_field(text: str, keys: list[str]) -> str:
    for key in keys:
        json_pattern = rf'"{re.escape(key)}"\s*:\s*"((?:\\.|[^"\\])*)"'
        match = re.search(json_pattern, text, flags=re.DOTALL)
        if match:
            return clean_whitespace(bytes(match.group(1), "utf-8").decode("unicode_escape"))

        line_pattern = rf'^{re.escape(key)}\s*=\s*(.+)$'
        match = re.search(line_pattern, text, flags=re.MULTILINE)
        if match:
            return clean_whitespace(match.group(1))
    return ""


def _extract_claims_field(text: str, keys: list[str]) -> list[str]:
    for key in keys:
        json_start = re.search(rf'"{re.escape(key)}"\s*:\s*\[', text, flags=re.DOTALL)
        if json_start:
            segment = text[json_start.end():]
            claims = re.findall(r'"((?:\\.|[^"\\])*)"', segment)
            cleaned = [clean_whitespace(bytes(claim, "utf-8").decode("unicode_escape")) for claim in claims]
            cleaned = [claim for claim in cleaned if claim]
            if cleaned:
                return cleaned[:2]

        line_pattern = rf'^{re.escape(key)}\s*=\s*(.+)$'
        match = re.search(line_pattern, text, flags=re.MULTILINE)
        if match:
            value = clean_whitespace(match.group(1))
            if not value or value in {"[]", "-", "none"}:
                return []
            claims = [clean_whitespace(part) for part in value.split("||") if clean_whitespace(part)]
            if claims:
                return claims[:2]
    return []


def _extract_scores_map(text: str, code_lookup: dict[str, str]) -> dict[str, int]:
    score_block_match = re.search(r'"(?:scores|g)"\s*:\s*\{(.*)', text, flags=re.DOTALL)
    segment = score_block_match.group(1) if score_block_match else text
    scores: dict[str, int] = {}
    for key, value in re.findall(r'"([^"]+)"\s*:\s*(-?\d+)', segment):
        criterion_name = _normalize_criterion_key(key, code_lookup)
        if criterion_name:
            scores[criterion_name] = _bounded_score_10(value, default=1)
    for key, value in re.findall(r'\b([a-z][a-z0-9_]*)\s*:\s*(-?\d+)', segment):
        criterion_name = _normalize_criterion_key(key, code_lookup)
        if criterion_name and criterion_name not in scores:
            scores[criterion_name] = _bounded_score_10(value, default=1)
    return scores


def _repair_json_text(text: str) -> str:
    repaired = _strip_code_fences(text)
    repaired = repaired.replace("“", '"').replace("”", '"').replace("’", "'")
    repaired = re.sub(r",\s*([}\]])", r"\1", repaired)
    repaired = re.sub(r",\s*$", "", repaired)
    open_braces = repaired.count("{")
    close_braces = repaired.count("}")
    open_brackets = repaired.count("[")
    close_brackets = repaired.count("]")
    if close_brackets < open_brackets:
        repaired += "]" * (open_brackets - close_brackets)
    if close_braces < open_braces:
        repaired += "}" * (open_braces - close_braces)
    return repaired


def _json_from_text(text: str) -> dict[str, Any]:
    stripped = _strip_code_fences(text)

    try:
        return json.loads(stripped)
    except json.JSONDecodeError:
        repaired = _repair_json_text(stripped)
        if repaired != stripped:
            try:
                return json.loads(repaired)
            except json.JSONDecodeError:
                pass

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
        decoder = json.JSONDecoder()
        start = repaired.find("{")
        while start != -1:
            try:
                obj, _ = decoder.raw_decode(repaired[start:])
                if isinstance(obj, dict):
                    return obj
            except json.JSONDecodeError:
                start = repaired.find("{", start + 1)
                continue
        raise


def _bounded_score_10(value: Any, default: int = 1) -> int:
    try:
        return max(1, min(10, int(value)))
    except (TypeError, ValueError):
        return default


def _normalize_claims(value: Any) -> list[str]:
    if isinstance(value, list):
        claims = [clean_whitespace(str(item)) for item in value if clean_whitespace(str(item))]
    elif isinstance(value, str) and clean_whitespace(value):
        claims = [clean_whitespace(value)]
    else:
        claims = []
    return claims[:4]


def _normalize_topic_hint(value: Any, story: Story) -> str:
    topic_hint = clean_whitespace(str(value or ""))
    if topic_hint:
        return shorten_for_display(topic_hint, max_length=80)
    title = clean_whitespace(str(story.get("title") or "Untitled topic"))
    return shorten_for_display(title or "Untitled topic", max_length=80)


def _normalize_summary(value: Any, story: Story) -> str:
    summary = clean_whitespace(str(value or ""))
    if summary:
        return shorten_for_display(summary, max_length=280)
    return _fallback_summary(story)


def _mentions_from_text(story: Story) -> list[str]:
    text = f"{story.get('title', '')} {story.get('body', '')}"
    matches = re.findall(r"\b[A-Z][A-Za-z0-9''-]{2,}\b", text)
    blocked = {
        "The", "This", "That", "Once", "Daily", "Edition", "Registering", "Already",
        "Enter", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday",
        "Monday", "World", "News", "Update", "Updates", "Latest", "Breaking",
    }
    seen: list[str] = []
    for match in matches:
        if match in blocked:
            continue
        if match not in seen:
            seen.append(match)
        if len(seen) >= 12:
            break
    return seen


def _fallback_summary(story: Story) -> str:
    body = clean_whitespace(str(story.get("body") or ""))
    if body:
        first_sentence = re.split(r"(?<=[.!?])\s+", body, maxsplit=1)[0]
        return shorten_for_display(clean_whitespace(first_sentence), max_length=280)
    return shorten_for_display(clean_whitespace(str(story.get("title") or "")), max_length=280)
