"""Text utilities."""

from __future__ import annotations

import re


def clean_text(text: str) -> str:
    """Clean extracted text: normalize whitespace, strip."""
    text = re.sub(r"\s+", " ", text)
    return text.strip()


def truncate(text: str, max_len: int = 5000) -> str:
    if len(text) <= max_len:
        return text
    return text[:max_len].rsplit(" ", 1)[0] + "..."


def word_count(text: str) -> int:
    return len(text.split())
