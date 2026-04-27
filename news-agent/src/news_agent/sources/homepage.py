"""Homepage scraping fallback for sources without RSS or when RSS fails."""

from __future__ import annotations

import logging
import re
from datetime import datetime, timezone
from urllib.parse import urljoin, urlparse

import requests
from bs4 import BeautifulSoup

from ..models.contracts import CandidateArticle

logger = logging.getLogger("news_agent.sources.homepage")


def fetch_homepage(homepage_url: str, source_name: str, user_agent: str,
                   max_links: int = 100, timeout: int = 30,
                   deny_patterns: list[str] | None = None,
                   prefer_patterns: list[str] | None = None) -> list[CandidateArticle]:
    """Scrape article links from a source homepage."""
    deny_patterns = deny_patterns or []
    prefer_patterns = prefer_patterns or []

    try:
        resp = requests.get(homepage_url, timeout=timeout,
                            headers={"User-Agent": user_agent})
        resp.raise_for_status()
    except Exception as e:
        logger.warning(f"Homepage fetch failed for {source_name}: {e}")
        return []

    soup = BeautifulSoup(resp.text, "lxml")
    base_domain = urlparse(homepage_url).netloc.lower()

    seen = set()
    candidates = []

    for a_tag in soup.find_all("a", href=True):
        href = a_tag["href"]
        url = urljoin(homepage_url, href)
        parsed = urlparse(url)

        # Only same-domain links
        if parsed.netloc.lower().replace("www.", "") != base_domain.replace("www.", ""):
            continue

        # Skip non-article paths
        path = parsed.path.lower()
        if any(skip in path for skip in [
            "/tag/", "/category/", "/author/", "/page/", "/search",
            "/login", "/register", "/subscribe", "/about", "/contact",
            "/privacy", "/terms", "/cookie", "#",
        ]):
            continue

        # Must look like an article (has path segments)
        if path.count("/") < 2 and not re.search(r"\d", path):
            continue

        # Apply deny patterns
        if any(re.search(p, url, re.IGNORECASE) for p in deny_patterns):
            continue

        if url in seen:
            continue
        seen.add(url)

        title = a_tag.get_text(strip=True)[:200] or ""

        candidates.append(CandidateArticle(
            url=url,
            source_name=source_name,
            title=title,
            published=None,
            discovered_at=datetime.now(timezone.utc),
        ))

    # Prioritize prefer-pattern matches
    if prefer_patterns:
        preferred = []
        others = []
        for c in candidates:
            if any(re.search(p, c.url, re.IGNORECASE) for p in prefer_patterns):
                preferred.append(c)
            else:
                others.append(c)
        candidates = preferred + others

    result = candidates[:max_links]
    logger.info(f"Homepage: {source_name} -> {len(result)} candidates")
    return result
