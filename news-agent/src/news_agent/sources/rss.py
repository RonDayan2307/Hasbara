"""RSS feed collection."""

from __future__ import annotations

import logging
import time
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime

import feedparser
import requests

from ..models.contracts import CandidateArticle

logger = logging.getLogger("news_agent.sources.rss")


def fetch_rss(rss_url: str, source_name: str, user_agent: str,
              max_links: int = 100, timeout: int = 30) -> list[CandidateArticle]:
    """Fetch candidates from an RSS feed."""
    candidates = []
    try:
        resp = requests.get(rss_url, timeout=timeout,
                            headers={"User-Agent": user_agent})
        resp.raise_for_status()
        feed = feedparser.parse(resp.text)
    except Exception as e:
        logger.warning(f"RSS fetch failed for {source_name}: {e}")
        return []

    for entry in feed.entries[:max_links]:
        link = entry.get("link", "")
        if not link:
            continue

        title = entry.get("title", "")
        published = None

        pub_str = entry.get("published") or entry.get("updated")
        if pub_str:
            try:
                published = parsedate_to_datetime(pub_str)
                if published.tzinfo is None:
                    published = published.replace(tzinfo=timezone.utc)
            except Exception:
                pass

        candidates.append(CandidateArticle(
            url=link,
            source_name=source_name,
            title=title,
            published=published,
            discovered_at=datetime.now(timezone.utc),
        ))

    logger.info(f"RSS: {source_name} -> {len(candidates)} candidates")
    return candidates
