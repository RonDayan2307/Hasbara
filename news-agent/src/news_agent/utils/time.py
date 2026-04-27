"""Time utilities."""

from __future__ import annotations

from datetime import datetime, timezone


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


def utcnow_iso() -> str:
    return utcnow().isoformat()


def is_within_hours(dt_str: str | None, hours: int) -> bool:
    """Check if an ISO datetime string is within N hours of now."""
    if not dt_str:
        return False
    try:
        dt = datetime.fromisoformat(dt_str)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return (utcnow() - dt).total_seconds() < hours * 3600
    except (ValueError, TypeError):
        return False
