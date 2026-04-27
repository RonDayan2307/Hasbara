"""Hashing utilities."""

from __future__ import annotations

import hashlib


def content_hash(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()[:16]


def cache_key(url: str, content_hash_val: str, model: str, prompt_version: str) -> str:
    raw = f"{url}|{content_hash_val}|{model}|{prompt_version}"
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()[:32]
