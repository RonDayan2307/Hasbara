"""Claim extraction for high-scoring articles."""

from __future__ import annotations

import logging

from ..models.contracts import Claim
from .ollama_client import OllamaClient
from .prompts import SYSTEM_ANALYST, CLAIM_EXTRACTION_PROMPT

logger = logging.getLogger("news_agent.analysis.claims")


def extract_claims(client: OllamaClient, article_id: int,
                   url: str, source_name: str, title: str,
                   body_text: str) -> list[Claim]:
    """Extract reputational-risk claims from an article."""
    prompt = CLAIM_EXTRACTION_PROMPT.format(
        url=url,
        source_name=source_name,
        title=title,
        body_text=body_text[:20000],
    )

    result = client.generate_json(prompt, system=SYSTEM_ANALYST)
    if result is None or not isinstance(result, dict):
        logger.warning(f"Claim extraction failed for {url}")
        return []

    claims = []
    for c in result.get("claims", []):
        if not isinstance(c, dict):
            continue
        claim_text = c.get("claim_text", "").strip()
        if not claim_text:
            continue
        claims.append(Claim(
            article_id=article_id,
            claim_text=claim_text,
            source_url=url,
            source_name=source_name,
            category=c.get("category", ""),
            target_entity=c.get("target_entity", ""),
            status=c.get("status", "needs_human_verification"),
            confidence=float(c.get("confidence", 0.0)),
            citation_url=url,
        ))

    logger.info(f"Extracted {len(claims)} claims from {url}")
    return claims
