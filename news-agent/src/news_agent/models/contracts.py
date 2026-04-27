"""Data contracts / typed dicts used across the pipeline."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum
from typing import Any


class TopicLifecycle(str, Enum):
    EMERGING = "emerging"
    GROWING = "growing"
    VIRAL = "viral"
    DECLINING = "declining"
    DORMANT = "dormant"
    RESURFACING = "resurfacing"


class ClaimStatus(str, Enum):
    VERIFIED = "verified"
    DISPUTED = "disputed"
    UNSUPPORTED = "unsupported"
    FALSE = "false"
    NEEDS_HUMAN = "needs_human_verification"


@dataclass
class SourceConfig:
    name: str
    homepage_url: str
    rss_url: str | None = None
    enabled: bool = True
    language: str = "en"
    region: str = "international"
    orientation: str = "center"
    credibility_level: str = "medium"
    priority: int = 2
    max_links: int = 20
    deny_patterns: list[str] = field(default_factory=list)
    prefer_patterns: list[str] = field(default_factory=list)


@dataclass
class CandidateArticle:
    url: str
    source_name: str
    title: str = ""
    published: datetime | None = None
    discovered_at: datetime | None = None


@dataclass
class Article:
    id: int | None = None
    url: str = ""
    canonical_url: str = ""
    source_name: str = ""
    title: str = ""
    author: str = ""
    published: datetime | None = None
    discovered_at: datetime | None = None
    body_text: str = ""
    word_count: int = 0
    language: str = "en"


@dataclass
class ArticleScores:
    article_id: int = 0
    url: str = ""
    criteria: dict[str, float] = field(default_factory=dict)
    final_score: float = 0.0
    override_triggered: bool = False
    override_reason: str = ""
    labels: list[str] = field(default_factory=list)
    confidence: float = 0.0
    model_name: str = ""
    prompt_version: str = ""
    rationale: str = ""


@dataclass
class Claim:
    id: int | None = None
    article_id: int = 0
    topic_id: int | None = None
    claim_text: str = ""
    source_url: str = ""
    source_name: str = ""
    category: str = ""
    target_entity: str = ""
    status: str = "needs_human_verification"
    confidence: float = 0.0
    citation_url: str = ""


@dataclass
class Topic:
    id: int | None = None
    name: str = ""
    summary: str = ""
    lifecycle: str = "emerging"
    final_score: float = 0.0
    labels: list[str] = field(default_factory=list)
    article_ids: list[int] = field(default_factory=list)
    claim_ids: list[int] = field(default_factory=list)
    source_names: list[str] = field(default_factory=list)
    first_seen: datetime | None = None
    last_updated: datetime | None = None
    article_count: int = 0


@dataclass
class Alert:
    topic_name: str = ""
    headline: str = ""
    risk_score: float = 0.0
    source_count: int = 0
    primary_sources: list[str] = field(default_factory=list)
    urls: list[str] = field(default_factory=list)
    reason: str = ""


@dataclass
class RunManifest:
    run_id: str = ""
    started_at: datetime | None = None
    finished_at: datetime | None = None
    status: str = "running"
    sources_checked: int = 0
    sources_failed: int = 0
    articles_collected: int = 0
    articles_scored: int = 0
    articles_reported: int = 0
    topics_found: int = 0
    alerts_raised: int = 0
    model_failures: int = 0
    degraded: bool = False
    errors: list[str] = field(default_factory=list)
