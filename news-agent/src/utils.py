from difflib import SequenceMatcher
import hashlib
import re
from pathlib import Path


def clean_whitespace(text: str) -> str:
    return re.sub(r"\s+", " ", text or "").strip()


def similarity(a: str, b: str) -> float:
    return SequenceMatcher(None, a.lower(), b.lower()).ratio()


def dedupe_stories(stories: list[dict], threshold: float = 0.82) -> list[dict]:
    kept = []
    for story in stories:
        if not is_duplicate_story(story, kept, threshold=threshold):
            kept.append(story)
    return kept


def is_duplicate_story(story: dict, existing_stories: list[dict], threshold: float = 0.82) -> bool:
    for existing in existing_stories:
        if similarity(story["title"], existing["title"]) >= threshold:
            return True
    return False


def project_root() -> Path:
    return Path(__file__).resolve().parent.parent


def stable_id(*parts: str, length: int = 16) -> str:
    joined = "\n".join(part or "" for part in parts)
    return hashlib.sha256(joined.encode("utf-8")).hexdigest()[:length]


def safe_filename(value: str, *, max_length: int = 80) -> str:
    lowered = (value or "").lower()
    normalized = re.sub(r"[^a-z0-9]+", "-", lowered).strip("-")
    normalized = re.sub(r"-{2,}", "-", normalized)
    return (normalized[:max_length].strip("-") or "untitled")
