"""Source configuration helpers."""

from __future__ import annotations

from ..models.contracts import SourceConfig


def load_sources(config: dict) -> list[SourceConfig]:
    """Load source configs from the main config dict."""
    sources = []
    for s in config.get("sources", []):
        sources.append(SourceConfig(
            name=s["name"],
            homepage_url=s["homepage_url"],
            rss_url=s.get("rss_url"),
            enabled=s.get("enabled", True),
            language=s.get("language", "en"),
            region=s.get("region", "international"),
            orientation=s.get("orientation", "center"),
            credibility_level=s.get("credibility_level", "medium"),
            priority=s.get("priority", 2),
            max_links=s.get("max_links", 100),
            deny_patterns=s.get("deny_patterns", []),
            prefer_patterns=s.get("prefer_patterns", []),
        ))
    return sources
