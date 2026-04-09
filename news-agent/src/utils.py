from difflib import SequenceMatcher
import re


def clean_whitespace(text: str) -> str:
    return re.sub(r"\s+", " ", text or "").strip()


def similarity(a: str, b: str) -> float:
    return SequenceMatcher(None, a.lower(), b.lower()).ratio()


def dedupe_stories(stories: list[dict], threshold: float = 0.82) -> list[dict]:
    kept = []
    for story in stories:
        duplicate = False
        for existing in kept:
            if similarity(story["title"], existing["title"]) >= threshold:
                duplicate = True
                break
        if not duplicate:
            kept.append(story)
    return kept