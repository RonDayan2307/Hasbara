from __future__ import annotations

import json
import logging
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Literal

from contracts import ReviewResult, Story

log = logging.getLogger(__name__)

FilterDecision = Literal["save", "reject", "normal"]

# Score thresholds — these define the three filtering tiers.
FULL_SAVE_AVG_MIN: float = 6.0   # average above this  → always save fully
FULL_SAVE_ANY_MIN: int = 8       # any single score >=  → always save fully
REJECT_AVG_MAX: float = 3.0      # average below this   → reject and record URL


class ArticleFilter:
    """Manages score-based filtering and a per-URL rejection store.

    Three-tier decision after a story is reviewed:

    * **save**   — average score > 6.0  OR  any single criterion score >= 8.
                   The story is saved fully and attached to topic memory.
    * **reject** — average score < 3.0.
                   The URL is persisted so it is skipped on future runs without
                   being sent to the model again.
    * **normal** — everything else; standard ``worth_reviewing`` logic applies.

    Per-source context
    ------------------
    Before processing each article ``get_source_rejected_count()`` returns how
    many URLs from the same source were previously rejected.  The pipeline logs
    this as context before the model call, so the operator can spot sources that
    consistently produce low-scoring content.
    """

    def __init__(self, path: Path) -> None:
        self.path = path
        # url → {source, avg_score, rejected_at}
        self._rejected: dict[str, dict[str, Any]] = {}
        # source_name → list of rejected urls (for per-source context)
        self._by_source: dict[str, list[str]] = {}

    # ── Lifecycle ─────────────────────────────────────────────────────────────

    @classmethod
    def load(cls, path: Path) -> "ArticleFilter":
        obj = cls(path)
        if path.exists():
            try:
                data = json.loads(path.read_text(encoding="utf-8"))
                for url, entry in data.get("rejected", {}).items():
                    obj._rejected[url] = entry
                    source = entry.get("source", "unknown")
                    obj._by_source.setdefault(source, []).append(url)
                log.info(
                    "Loaded article filter: %d rejected URL(s) from %s",
                    len(obj._rejected),
                    path,
                )
            except Exception as exc:
                log.warning(
                    "Could not load article filter from %s: %s. Starting fresh.",
                    path,
                    exc,
                )
        return obj

    def save(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        cutoff = datetime.now(timezone.utc) - timedelta(days=14)
        active: dict[str, dict[str, Any]] = {}
        pruned = 0
        for url, entry in self._rejected.items():
            rejected_at = entry.get("rejected_at")
            if rejected_at:
                try:
                    dt = datetime.fromisoformat(rejected_at.replace("Z", "+00:00"))
                    if dt.tzinfo is None:
                        dt = dt.replace(tzinfo=timezone.utc)
                    if dt >= cutoff:
                        active[url] = entry
                    else:
                        pruned += 1
                except ValueError:
                    active[url] = entry
            else:
                active[url] = entry
        if pruned:
            log.info("Pruned %d expired rejected URL(s) (older than 14 days).", pruned)
        self._rejected = active
        self._by_source = {}
        for url, entry in self._rejected.items():
            source = entry.get("source", "unknown")
            self._by_source.setdefault(source, []).append(url)
        payload = {
            "schema_version": 1,
            "count": len(self._rejected),
            "rejected": self._rejected,
        }
        self.path.write_text(
            json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8"
        )
        log.info(
            "Saved article filter: %d rejected URL(s) to %s",
            len(self._rejected),
            self.path,
        )

    # ── Pre-processing checks ─────────────────────────────────────────────────

    def is_rejected(self, url: str) -> bool:
        """Return True if this URL was previously rejected (avg score < 3)."""
        return url in self._rejected

    def get_source_rejected_count(self, source_name: str) -> int:
        """Return how many URLs from this source were previously rejected."""
        return len(self._by_source.get(source_name, []))

    # ── Post-review decision ──────────────────────────────────────────────────

    def classify(self, review: ReviewResult) -> FilterDecision:
        """Classify a review result into a filter decision.

        Returns:
            ``'save'``   — high average or any standout score; force full save.
            ``'reject'`` — uniformly low scores; record URL to skip next time.
            ``'normal'`` — middle range; use the standard ``worth_reviewing`` flag.
        """
        avg: float = review["score_summary"]["average_score"]
        max_s: float = review["score_summary"]["max_score"]

        if avg > FULL_SAVE_AVG_MIN or max_s >= FULL_SAVE_ANY_MIN:
            return "save"
        if avg < REJECT_AVG_MAX:
            return "reject"
        return "normal"

    def record(self, story: Story, review: ReviewResult, decision: FilterDecision) -> None:
        """Persist a rejection entry for future runs.

        Only called when ``decision == 'reject'``.  Safe to call for other
        decisions — it becomes a no-op.
        """
        if decision != "reject":
            return
        url = story.get("url", "")
        if not url:
            return
        source = story.get("source", "unknown")
        avg = review["score_summary"]["average_score"]
        entry: dict[str, Any] = {
            "source": source,
            "avg_score": avg,
            "rejected_at": datetime.now(timezone.utc).replace(microsecond=0).isoformat(),
        }
        self._rejected[url] = entry
        self._by_source.setdefault(source, []).append(url)
        log.info(
            "Rejected article: avg=%.1f source=%s url=%s",
            avg,
            source,
            url,
        )

    # ── Stats ─────────────────────────────────────────────────────────────────

    @property
    def rejected_count(self) -> int:
        return len(self._rejected)
