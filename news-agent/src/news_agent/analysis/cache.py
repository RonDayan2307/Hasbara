"""Model response caching."""

from __future__ import annotations

import logging

from ..db.repositories import CacheRepo
from ..utils.hashing import cache_key

logger = logging.getLogger("news_agent.analysis.cache")


class AnalysisCache:
    def __init__(self, cache_repo: CacheRepo, model_name: str, prompt_version: str):
        self.repo = cache_repo
        self.model_name = model_name
        self.prompt_version = prompt_version

    def get(self, url: str, content_hash_val: str) -> dict | None:
        key = cache_key(url, content_hash_val, self.model_name, self.prompt_version)
        return self.repo.get(key)

    def set(self, url: str, content_hash_val: str, response: dict) -> None:
        key = cache_key(url, content_hash_val, self.model_name, self.prompt_version)
        self.repo.set(key, response, self.model_name, self.prompt_version)
