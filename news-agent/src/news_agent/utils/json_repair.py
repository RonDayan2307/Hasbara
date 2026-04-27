"""JSON repair utilities for handling malformed model outputs."""

from __future__ import annotations

import json
import re


def extract_json(text: str) -> str | None:
    """Try to extract a JSON object or array from text that may contain extra content."""
    # Try direct parse first
    text = text.strip()
    try:
        json.loads(text)
        return text
    except json.JSONDecodeError:
        pass

    # Look for ```json blocks
    m = re.search(r"```json\s*([\s\S]*?)```", text)
    if m:
        candidate = m.group(1).strip()
        try:
            json.loads(candidate)
            return candidate
        except json.JSONDecodeError:
            pass

    # Find first { or [ and match to last } or ]
    for open_c, close_c in [("{", "}"), ("[", "]")]:
        start = text.find(open_c)
        if start == -1:
            continue
        end = text.rfind(close_c)
        if end == -1 or end <= start:
            continue
        candidate = text[start:end + 1]
        try:
            json.loads(candidate)
            return candidate
        except json.JSONDecodeError:
            pass

    return None


def safe_parse_json(text: str) -> dict | list | None:
    """Try to parse JSON from model output, with repair attempts."""
    extracted = extract_json(text)
    if extracted is None:
        return None
    try:
        return json.loads(extracted)
    except json.JSONDecodeError:
        # Try common fixes
        fixed = extracted
        # Trailing commas before } or ]
        fixed = re.sub(r",\s*([}\]])", r"\1", fixed)
        # Single quotes to double
        fixed = fixed.replace("'", '"')
        try:
            return json.loads(fixed)
        except json.JSONDecodeError:
            return None
