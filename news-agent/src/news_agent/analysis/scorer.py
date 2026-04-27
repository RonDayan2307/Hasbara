"""Article scoring using the Ollama model."""

from __future__ import annotations

import logging

from ..models.contracts import ArticleScores
from .ollama_client import OllamaClient
from .prompts import SYSTEM_ANALYST, CLASSIFICATION_PROMPT
from .cache import AnalysisCache

logger = logging.getLogger("news_agent.analysis.scorer")


def score_article(client: OllamaClient, cache: AnalysisCache | None,
                  url: str, source_name: str, orientation: str,
                  credibility_level: str, title: str,
                  body_text: str, content_hash_val: str,
                  config: dict) -> ArticleScores | None:
    """Score an article using the LLM."""
    # Check cache first
    if cache:
        cached = cache.get(url, content_hash_val)
        if cached:
            logger.debug(f"Cache hit for {url}")
            return _parse_scores(cached, url, config)

    prompt = CLASSIFICATION_PROMPT.format(
        url=url,
        source_name=source_name,
        orientation=orientation,
        credibility_level=credibility_level,
        title=title,
        body_text=body_text[:20000],
    )

    result = client.generate_json(prompt, system=SYSTEM_ANALYST)
    if result is None:
        logger.warning(f"Model failed to score: {url}")
        return None

    # Cache successful result
    if cache and isinstance(result, dict):
        cache.set(url, content_hash_val, result)

    return _parse_scores(result, url, config)


def _parse_scores(data: dict, url: str, config: dict) -> ArticleScores | None:
    """Parse model output into ArticleScores."""
    if not isinstance(data, dict):
        return None

    scores_dict = data.get("scores", {})
    if not scores_dict:
        return None

    criteria = {}
    for criterion in config.get("scoring_criteria", []):
        val = scores_dict.get(criterion, 0)
        try:
            criteria[criterion] = float(val)
        except (ValueError, TypeError):
            criteria[criterion] = 0.0

    # Compute final score: average + count of criteria >= 7
    values = list(criteria.values())
    if not values:
        return None

    avg = sum(values) / len(values)
    high_count = sum(1 for v in values if v >= 7)
    final_score = min(10.0, avg + high_count)

    # Check override triggers
    override_triggered = False
    override_reason = ""
    for trigger in config.get("override_triggers", []):
        criterion = trigger["criterion"]
        minimum = trigger["minimum"]
        if criteria.get(criterion, 0) >= minimum:
            override_triggered = True
            override_reason = f"{criterion} >= {minimum}"
            break

    labels = data.get("labels", [])
    confidence = data.get("confidence", 0.0)
    rationale = data.get("rationale", "")

    return ArticleScores(
        url=url,
        criteria=criteria,
        final_score=round(final_score, 2),
        override_triggered=override_triggered,
        override_reason=override_reason,
        labels=labels if isinstance(labels, list) else [],
        confidence=float(confidence) if confidence else 0.0,
        model_name=config.get("model", {}).get("name", ""),
        prompt_version=config.get("prompt_versions", {}).get("classification", "v1"),
        rationale=str(rationale)[:5000],
    )
