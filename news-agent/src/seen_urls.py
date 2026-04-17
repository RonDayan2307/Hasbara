from __future__ import annotations

import json
import logging
from datetime import datetime, timedelta, timezone
from pathlib import Path

log = logging.getLogger(__name__)

_TTL_DAYS = 14


class SeenUrlStore:
    """Persistent cross-run store of all article URLs that have been scraped.

    Checked BEFORE fetching an article body to avoid re-fetching content
    that was already processed in a previous run.  Only successfully yielded
    articles are recorded — skipped or failed URLs are intentionally excluded
    so they can be retried if the failure was transient.

    Entries older than 14 days are pruned on save, matching the topic and
    review-cache TTL windows.
    """

    def __init__(self, path: Path) -> None:
        self.path = path
        # url → {source, seen_at}
        self._seen: dict[str, dict] = {}

    # ── Lifecycle ─────────────────────────────────────────────────────────────

    @classmethod
    def load(cls, path: Path) -> "SeenUrlStore":
        obj = cls(path)
        if path.exists():
            try:
                data = json.loads(path.read_text(encoding="utf-8"))
                obj._seen = data.get("seen", {})
                log.info(
                    "Loaded seen-URL store: %d URL(s) from %s",
                    len(obj._seen),
                    path,
                )
            except Exception as exc:
                log.warning(
                    "Could not load seen-URL store from %s: %s. Starting fresh.",
                    path,
                    exc,
                )
        return obj

    def save(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        cutoff = datetime.now(timezone.utc) - timedelta(days=_TTL_DAYS)
        active: dict[str, dict] = {}
        pruned = 0
        for url, entry in self._seen.items():
            seen_at = entry.get("seen_at")
            if seen_at:
                try:
                    dt = datetime.fromisoformat(seen_at.replace("Z", "+00:00"))
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
            log.info(
                "Pruned %d expired seen-URL entries (older than %d days).",
                pruned,
                _TTL_DAYS,
            )
        self._seen = active
        payload = {
            "schema_version": 1,
            "count": len(self._seen),
            "seen": self._seen,
        }
        self.path.write_text(
            json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8"
        )
        log.info("Saved seen-URL store: %d URL(s) to %s", len(self._seen), self.path)

    # ── Lookups ───────────────────────────────────────────────────────────────

    def is_seen(self, url: str) -> bool:
        """Return True if this URL was already scraped in a previous run."""
        return bool(url) and url in self._seen

    def add(self, url: str, *, source_name: str = "unknown") -> None:
        """Record a URL as seen. Safe to call with empty or duplicate URLs."""
        if url and url not in self._seen:
            self._seen[url] = {
                "source": source_name,
                "seen_at": datetime.now(timezone.utc).replace(microsecond=0).isoformat(),
            }

    @property
    def count(self) -> int:
        return len(self._seen)
