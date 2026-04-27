"""Topic lifecycle state management."""

from __future__ import annotations


def determine_lifecycle(article_count: int, source_count: int,
                        prev_lifecycle: str | None,
                        prev_article_count: int = 0) -> str:
    """Determine topic lifecycle state based on growth signals."""
    if prev_lifecycle is None or prev_lifecycle == "dormant":
        if article_count > prev_article_count:
            return "resurfacing" if prev_lifecycle == "dormant" else "emerging"

    if article_count >= 10 or source_count >= 5:
        return "viral"
    elif article_count >= 5 or source_count >= 3:
        return "growing"
    elif article_count > prev_article_count:
        if prev_lifecycle in ("viral", "growing"):
            return prev_lifecycle
        return "emerging"
    elif article_count == prev_article_count:
        if prev_lifecycle in ("viral", "growing"):
            return "declining"
        return prev_lifecycle or "emerging"

    return prev_lifecycle or "emerging"


def severity_label(final_score: float) -> str:
    """Map final score to severity label."""
    if final_score >= 9.0:
        return "Critical"
    elif final_score >= 7.0:
        return "High"
    elif final_score >= 5.0:
        return "Medium"
    else:
        return "Low"
