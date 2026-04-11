from __future__ import annotations

from dataclasses import asdict, dataclass, field


@dataclass
class SourceHealth:
    source: str
    homepage: str
    priority: int
    language: str
    orientation: str
    links_found: int = 0
    stories_collected: int = 0
    candidate_skips: int = 0
    article_extraction_failures: int = 0
    homepage_status: str = "pending"
    homepage_error: str = ""
    notes: list[str] = field(default_factory=list)

    def note(self, message: str) -> None:
        if message and message not in self.notes:
            self.notes.append(message)

    def to_dict(self) -> dict:
        payload = asdict(self)
        payload["status"] = self.status
        return payload

    @property
    def status(self) -> str:
        if self.homepage_error:
            return "failed"
        if self.stories_collected > 0:
            return "ok"
        if self.links_found > 0 or self.candidate_skips > 0 or self.article_extraction_failures > 0:
            return "partial"
        return self.homepage_status


@dataclass
class IngestionTelemetry:
    source_health: dict[str, SourceHealth] = field(default_factory=dict)

    def ensure_source(self, source: dict) -> SourceHealth:
        name = str(source.get("name", "unknown"))
        health = self.source_health.get(name)
        if health is None:
            health = SourceHealth(
                source=name,
                homepage=str(source.get("homepage", "")),
                priority=int(source.get("priority", 0) or 0),
                language=str(source.get("language", "unknown")),
                orientation=str(source.get("orientation", "unknown")),
            )
            self.source_health[name] = health
        return health

    def record_homepage_success(self, source: dict, *, links_found: int) -> None:
        health = self.ensure_source(source)
        health.homepage_status = "ok"
        health.links_found = links_found
        health.homepage_error = ""

    def record_homepage_failure(self, source: dict, error: str) -> None:
        health = self.ensure_source(source)
        health.homepage_status = "failed"
        health.homepage_error = error
        health.note(error)

    def record_candidate_skip(self, source: dict, reason: str) -> None:
        health = self.ensure_source(source)
        health.candidate_skips += 1
        health.note(reason)

    def record_extraction_failure(self, source: dict, error: str) -> None:
        health = self.ensure_source(source)
        health.article_extraction_failures += 1
        health.note(error)

    def record_story_collected(self, source: dict) -> None:
        health = self.ensure_source(source)
        health.stories_collected += 1

    @property
    def source_failures(self) -> int:
        return sum(1 for health in self.source_health.values() if health.homepage_error)

    @property
    def article_extraction_failures(self) -> int:
        return sum(health.article_extraction_failures for health in self.source_health.values())

    @property
    def candidate_skips(self) -> int:
        return sum(health.candidate_skips for health in self.source_health.values())

    def as_list(self) -> list[dict]:
        items = [health.to_dict() for health in self.source_health.values()]
        return sorted(items, key=lambda item: (-int(item.get("priority", 0)), item.get("source", "")))
