"""Robots.txt awareness."""

from __future__ import annotations

import logging
from urllib.parse import urlparse
from urllib.robotparser import RobotFileParser

import requests

logger = logging.getLogger("news_agent.sources.robots")

_cache: dict[str, RobotFileParser] = {}


def can_fetch(url: str, user_agent: str) -> bool:
    """Check if we can fetch a URL according to robots.txt."""
    parsed = urlparse(url)
    base = f"{parsed.scheme}://{parsed.netloc}"

    if base not in _cache:
        rp = RobotFileParser()
        robots_url = f"{base}/robots.txt"
        try:
            resp = requests.get(robots_url, timeout=10,
                                headers={"User-Agent": user_agent})
            if resp.status_code == 200:
                rp.parse(resp.text.splitlines())
            else:
                # No robots.txt or error = allow all
                rp.allow_all = True
        except Exception as e:
            logger.debug(f"Failed to fetch robots.txt for {base}: {e}")
            rp.allow_all = True
        _cache[base] = rp

    rp = _cache[base]
    if getattr(rp, "allow_all", False):
        return True
    return rp.can_fetch(user_agent, url)


def clear_cache() -> None:
    _cache.clear()
