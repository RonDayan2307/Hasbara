from __future__ import annotations

import json
import logging
from copy import deepcopy
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

log = logging.getLogger(__name__)

_TTL_DAYS = 14


class ReviewCache:
    """Persists model reviews keyed by story_id.

    On repeated runs, stories that were already reviewed by the model are
    returned from cache instead of being sent to Ollama again. Heuristic
    fallback reviews are NOT cached — only successful model reviews are stored.
    """

    def __init__(self, path: Path, namespace: str) -> None:
        self.path = path
        self.namespace = namespace
        self._cache: dict[str, dict[str, Any]] = {}
        self._hits = 0
        self._misses = 0

    @classmethod
    def load(cls, path: Path, namespace: str) -> "ReviewCache":
        obj = cls(path, namespace)
        if path.exists():
            try:
                data = json.loads(path.read_text(encoding="utf-8"))
                cache_namespace = data.get("namespace")
                if cache_namespace != namespace:
                    log.info(
                        "Ignoring stale review cache namespace %s from %s; expected %s.",
                        cache_namespace or "<missing>",
                        path,
                        namespace,
                    )
                else:
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
            cached_review = deepcopy(review)
            cached_review["original_review_method"] = review.get("review_method", "model")
            cached_review["review_method"] = "cached"
            return cached_review
        else:
            self._misses += 1
        return None

    def put(self, story_id: str, review: dict[str, Any]) -> None:
        stored = deepcopy(review)
        stored["review_method"] = "model"
        self._cache[story_id] = stored

    def save(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        cutoff = datetime.now(timezone.utc) - timedelta(days=_TTL_DAYS)
        active: dict[str, dict[str, Any]] = {}
        pruned = 0
        for story_id, review in self._cache.items():
            reviewed_at = review.get("reviewed_at")
            if reviewed_at:
                try:
                    dt = datetime.fromisoformat(reviewed_at.replace("Z", "+00:00"))
                    if dt.tzinfo is None:
                        dt = dt.replace(tzinfo=timezone.utc)
                    if dt >= cutoff:
                        active[story_id] = review
                    else:
                        pruned += 1
                except ValueError:
                    active[story_id] = review
            else:
                active[story_id] = review
        if pruned:
            log.info("Pruned %d stale review cache entries (older than %d days).", pruned, _TTL_DAYS)
        self._cache = active
        payload = {
            "schema_version": 2,
            "namespace": self.namespace,
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
