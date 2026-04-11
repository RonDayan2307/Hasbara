from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Any

log = logging.getLogger(__name__)


class ReviewCache:
    """Persists model reviews keyed by story_id.

    On repeated runs, stories that were already reviewed by the model are
    returned from cache instead of being sent to Ollama again. Heuristic
    fallback reviews are NOT cached — only successful model reviews are stored.
    """

    def __init__(self, path: Path) -> None:
        self.path = path
        self._cache: dict[str, dict[str, Any]] = {}
        self._hits = 0
        self._misses = 0

    @classmethod
    def load(cls, path: Path) -> "ReviewCache":
        obj = cls(path)
        if path.exists():
            try:
                data = json.loads(path.read_text(encoding="utf-8"))
                obj._cache = data.get("reviews", {})
                log.info(
                    "Loaded review cache: %d cached reviews from %s",
                    len(obj._cache),
                    path,
                )
            except Exception as exc:
                log.warning(
                    "Could not load review cache from %s: %s. Starting fresh.",
                    path,
                    exc,
                )
        return obj

    def get(self, story_id: str) -> dict[str, Any] | None:
        review = self._cache.get(story_id)
        if review is not None:
            self._hits += 1
            log.debug("Cache hit for story_id=%s", story_id)
        else:
            self._misses += 1
        return review

    def put(self, story_id: str, review: dict[str, Any]) -> None:
        self._cache[story_id] = review

    def save(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        payload = {
            "schema_version": 1,
            "count": len(self._cache),
            "reviews": self._cache,
        }
        self.path.write_text(
            json.dumps(payload, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        log.info(
            "Saved review cache: %d reviews to %s (hits=%d misses=%d)",
            len(self._cache),
            self.path,
            self._hits,
            self._misses,
        )

    @property
    def hit_count(self) -> int:
        return self._hits

    @property
    def miss_count(self) -> int:
        return self._misses
