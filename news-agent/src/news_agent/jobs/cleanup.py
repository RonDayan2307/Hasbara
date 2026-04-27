"""Data retention cleanup."""

from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone

from ..db.connection import get_connection
from ..db.repositories import ArticleTextRepo, DebugRepo

logger = logging.getLogger("news_agent.jobs.cleanup")


def run_cleanup(config: dict) -> dict:
    """Run data retention cleanup."""
    db_path = config["paths"]["database"]
    conn = get_connection(db_path)

    retention = config.get("retention", {})
    text_days = retention.get("article_text_days", 14)
    debug_days = retention.get("debug_payload_days", 14)

    text_repo = ArticleTextRepo(conn)
    debug_repo = DebugRepo(conn)

    # Cleanup expired article texts
    expired_texts = text_repo.cleanup_expired()

    # Cleanup old debug events
    expired_debug = debug_repo.cleanup(debug_days)

    # Cleanup model cache older than 30 days
    cutoff = (datetime.now(timezone.utc) - timedelta(days=30)).isoformat()
    cur = conn.execute("DELETE FROM model_cache WHERE created_at < ?", (cutoff,))
    expired_cache = cur.rowcount
    conn.commit()

    conn.close()

    result = {
        "expired_texts_deleted": expired_texts,
        "debug_events_deleted": expired_debug,
        "cache_entries_deleted": expired_cache,
    }

    print(f"Cleanup complete:")
    print(f"  Expired article texts: {expired_texts}")
    print(f"  Debug events: {expired_debug}")
    print(f"  Cache entries: {expired_cache}")

    return result
