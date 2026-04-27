"""Conservative fallback scoring when the model fails."""

from __future__ import annotations

import logging
import re

from ..models.contracts import ArticleScores

logger = logging.getLogger("news_agent.analysis.fallback")

# Keywords that suggest relevance
RELEVANCE_KEYWORDS = [
    r"\bisrael\b", r"\bisraeli\b", r"\bidf\b", r"\bgaza\b",
    r"\bwest\s*bank\b", r"\bnetanyahu\b", r"\bjewish\b",
    r"\bzionism\b", r"\bzionist\b", r"\bantisemit", r"\bmossad\b",
    r"\bshin\s*bet\b", r"\bshabak\b", r"\bhamas\b", r"\bhezbollah\b",
    r"\bpalestini", r"\bicc\b", r"\bicj\b", r"\bbds\b",
]


def fallback_score(url: str, title: str, body_text: str,
                   config: dict) -> ArticleScores:
    """Produce a conservative fallback score based on keyword matching."""
    text = f"{title} {body_text}".lower()

    matches = sum(1 for kw in RELEVANCE_KEYWORDS if re.search(kw, text))

    # Conservative: score based on keyword density
    relevance = min(10, matches * 1.5)

    criteria = {}
    for criterion in config.get("scoring_criteria", []):
        if criterion == "israel_political_relevance":
            criteria[criterion] = round(relevance, 1)
        else:
            criteria[criterion] = 0.0

    values = list(criteria.values())
    avg = sum(values) / len(values) if values else 0
    high_count = sum(1 for v in values if v >= 7)
    final_score = min(10.0, avg + high_count)

    return ArticleScores(
        url=url,
        criteria=criteria,
        final_score=round(final_score, 2),
        override_triggered=False,
        labels=["fallback_scoring"],
        confidence=0.2,
        model_name="fallback",
        prompt_version="fallback",
        rationale="Keyword-based fallback due to model failure",
    )
