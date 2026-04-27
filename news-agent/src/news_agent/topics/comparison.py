"""Source comparison for topics."""

from __future__ import annotations

import json
import logging

from ..analysis.ollama_client import OllamaClient
from ..analysis.prompts import SYSTEM_ANALYST, SOURCE_COMPARISON_PROMPT

logger = logging.getLogger("news_agent.topics.comparison")


def compare_sources(client: OllamaClient, topic_name: str,
                    topic_summary: str,
                    articles: list[dict]) -> dict:
    """Compare how different sources cover a topic."""
    if len(articles) < 2:
        return {
            "comparison_summary": "Single source coverage.",
            "framing_differences": [],
            "missing_context": [],
            "notable_bias": [],
        }

    coverage = []
    for art in articles[:10]:  # Limit to avoid huge prompts
        coverage.append({
            "source": art.get("source_name", ""),
            "title": art.get("title", ""),
            "url": art.get("url", ""),
        })

    prompt = SOURCE_COMPARISON_PROMPT.format(
        topic_name=topic_name,
        topic_summary=topic_summary,
        source_coverage_json=json.dumps(coverage, indent=2),
    )

    result = client.generate_json(prompt, system=SYSTEM_ANALYST, temperature=0.3)
    if result is None or not isinstance(result, dict):
        return {
            "comparison_summary": "Source comparison unavailable.",
            "framing_differences": [],
            "missing_context": [],
            "notable_bias": [],
        }

    return {
        "comparison_summary": result.get("comparison_summary", ""),
        "framing_differences": result.get("framing_differences", []),
        "missing_context": result.get("missing_context", []),
        "notable_bias": result.get("notable_bias", []),
    }
