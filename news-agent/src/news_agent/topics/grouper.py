"""Topic grouping and management."""

from __future__ import annotations

import json
import logging

from ..analysis.ollama_client import OllamaClient
from ..analysis.prompts import SYSTEM_ANALYST, TOPIC_GROUPING_PROMPT
from ..db.repositories import TopicRepo, ScoreRepo

logger = logging.getLogger("news_agent.topics.grouper")


def group_articles_into_topics(
    client: OllamaClient,
    articles: list[dict],
    score_repo: ScoreRepo,
    topic_repo: TopicRepo,
) -> list[dict]:
    """Group scored articles into topics using the model."""
    if not articles:
        return []

    # Prepare article summaries for the model
    article_summaries = []
    for i, art in enumerate(articles):
        scores = score_repo.get_by_article(art["id"])
        labels = scores.get("labels", []) if scores else []
        article_summaries.append({
            "index": i,
            "title": art.get("title", ""),
            "source": art.get("source_name", ""),
            "url": art.get("url", ""),
            "score": art.get("final_score", 0),
            "labels": labels,
        })

    # Get existing active topics
    existing_topics = topic_repo.get_active()
    existing_summaries = [
        {"id": t["id"], "name": t["name"], "lifecycle": t.get("lifecycle", "emerging")}
        for t in existing_topics
    ]

    prompt = TOPIC_GROUPING_PROMPT.format(
        articles_json=json.dumps(article_summaries, indent=2),
        existing_topics_json=json.dumps(existing_summaries, indent=2),
    )

    result = client.generate_json(prompt, system=SYSTEM_ANALYST, temperature=0.2)
    if result is None or not isinstance(result, dict):
        logger.warning("Topic grouping model call failed, using single-topic fallback")
        return _fallback_grouping(articles)

    topics = result.get("topics", [])
    if not topics:
        return _fallback_grouping(articles)

    grouped = []
    for t in topics:
        if not isinstance(t, dict):
            continue
        indices = t.get("article_indices", [])
        grouped.append({
            "topic_name": t.get("topic_name", "Unnamed Topic"),
            "summary": t.get("summary", ""),
            "article_indices": [i for i in indices if isinstance(i, int) and 0 <= i < len(articles)],
            "existing_topic_id": t.get("existing_topic_id"),
            "lifecycle": t.get("lifecycle", "emerging"),
            "labels": t.get("labels", []),
        })

    return grouped


def _fallback_grouping(articles: list[dict]) -> list[dict]:
    """Simple fallback: each article is its own topic."""
    grouped = []
    for i, art in enumerate(articles):
        grouped.append({
            "topic_name": art.get("title", f"Article {i}"),
            "summary": art.get("title", ""),
            "article_indices": [i],
            "existing_topic_id": None,
            "lifecycle": "emerging",
            "labels": [],
        })
    return grouped
