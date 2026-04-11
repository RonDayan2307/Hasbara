from __future__ import annotations

from typing import Any, Literal, TypedDict


ReviewMethod = Literal["model", "cached", "heuristic_fallback"]
Priority = Literal["ignore", "low", "medium", "high", "breaking"]
ReviewQuality = Literal["high_confidence", "low_confidence", "fallback"]


class CriterionScore(TypedDict):
    criterion: str
    material: str
    score: int
    reason: str


class Story(TypedDict):
    id: str
    source: str
    source_language: str
    source_orientation: str
    source_priority: int
    title: str
    subtitle: str | None
    url: str
    body: str
    description: str | None
    published_at: str | None
    collected_at: str
    metrics: dict[str, Any]


class ReviewResult(TypedDict):
    story_id: str
    reviewed_at: str
    model: str
    review_method: ReviewMethod
    review_quality: ReviewQuality
    worth_reviewing: bool
    priority: Priority
    criteria_scores: list[CriterionScore]
    score_summary: dict[str, float]
    source_language: str
    political_orientation: str
    mentions: list[str]
    topic_hint: str
    summary: str
    claims_to_verify: list[str]
    review_reason: str
    confidence: str
    narrative_frame: str
    prompt_version: str
    normalization_version: str
    cache_namespace: str


class TopicAttachment(TypedDict):
    topic: dict[str, Any]
    topic_status: str
    cross_check: dict[str, Any]


class ReviewedItem(TypedDict):
    story: Story
    review: ReviewResult
    topic_status: str
    topic: dict[str, Any]
    cross_check: dict[str, Any]


class RunManifest(TypedDict):
    schema_version: int
    status: str
    run_id: str
    generated_at: str
    settings_file: str
    source_config_file: str
    source_rules_file: str
    criteria_file: str
    artifacts: dict[str, str]
    counts: dict[str, int | float]
    source_health: list[dict[str, Any]]
    reviewed_story_ids: list[str]
    report_mode: str
    local_ai_model: str
    min_usable_review_ratio: float
    cache_namespace: str
    prompt_version: str
    normalization_version: str
