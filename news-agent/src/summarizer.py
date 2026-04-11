import logging
import os
import json
import time

import requests
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry

log = logging.getLogger(__name__)

OLLAMA_URL = os.getenv("OLLAMA_URL", "http://localhost:11434/api/chat")
MODEL_NAME = os.getenv("OLLAMA_MODEL", "gemma4:e4b")
REQUEST_TIMEOUT_SECONDS = int(os.getenv("OLLAMA_TIMEOUT_SECONDS", "900"))
MAX_SUMMARY_STORIES = int(os.getenv("NEWS_MAX_SUMMARY_STORIES", "1"))
MAX_BODY_CHARS = int(os.getenv("NEWS_MAX_BODY_CHARS", "1600"))
NUM_PREDICT = int(os.getenv("OLLAMA_NUM_PREDICT", "280"))
NUM_CTX = int(os.getenv("OLLAMA_NUM_CTX", "2048"))
STREAM_CHAT = os.getenv("OLLAMA_STREAM", "1") == "1"
PROGRESS_LOG_SECONDS = int(os.getenv("OLLAMA_PROGRESS_SECONDS", "15"))

# Retry only transient connection/status failures.
# Do not retry read timeouts, otherwise long generations can stall for a very long time.
_RETRY = Retry(
    total=2,
    connect=2,
    read=0,
    backoff_factor=1,
    status_forcelist=[429, 500, 502, 503, 504],
    allowed_methods=["POST"],
    raise_on_status=False,
)


def _make_session() -> requests.Session:
    session = requests.Session()
    adapter = HTTPAdapter(max_retries=_RETRY)
    session.mount("http://", adapter)
    session.mount("https://", adapter)
    return session


_SESSION = _make_session()


def build_prompt(stories: list[dict]) -> str:
    blocks = []

    for i, story in enumerate(stories, start=1):
        body = story.get("body", "")[:MAX_BODY_CHARS]
        blocks.append(
            f"""STORY {i}
Source: {story['source']}
Title: {story['title']}
URL: {story['url']}
Body:
{body}
"""
        )

    joined = "\n\n".join(blocks)

    if len(stories) == 1:
        return f"""
You are producing a factual summary of one news story.

Rules:
- Use only the provided story.
- Do not invent facts.
- Keep the tone neutral.
- Keep output concise.

Output format:

Summary
- 3 to 5 bullets, 1 sentence each

Why It Matters
- 2 short bullets

Stories:
{joined}
""".strip()

    return f"""
You are producing a factual daily news digest.

Rules:
- Use only the provided stories.
- Do not invent facts.
- Group related stories together.
- Merge duplicate coverage.
- Keep the tone neutral.

Output format:

Top Stories
- 4 to 8 bullets, 2-4 sentences each

Quick Headlines
- short bullets

Watchlist
- 3 to 5 bullets on developments to monitor

Stories:
{joined}
""".strip()


def _summarize_streaming(payload: dict) -> str:
    log.info("Streaming summary from Ollama... waiting for first tokens.")
    chunks = []
    chars = 0
    last_progress = time.monotonic()
    first_token_logged = False

    with _SESSION.post(
        OLLAMA_URL,
        json={**payload, "stream": True},
        timeout=(10, REQUEST_TIMEOUT_SECONDS),
        stream=True,
    ) as response:
        response.raise_for_status()

        for raw_line in response.iter_lines(decode_unicode=True):
            if not raw_line:
                continue
            try:
                data = json.loads(raw_line)
            except json.JSONDecodeError:
                continue

            if data.get("error"):
                raise RuntimeError(f"Ollama error: {data['error']}")

            token = data.get("message", {}).get("content", "")
            if token:
                chunks.append(token)
                chars += len(token)
                if not first_token_logged:
                    log.info("First tokens received from Ollama.")
                    first_token_logged = True

            now = time.monotonic()
            if now - last_progress >= PROGRESS_LOG_SECONDS:
                log.info("Summarizer progress: %d characters generated...", chars)
                last_progress = now

            if data.get("done"):
                break

    return "".join(chunks).strip()


def _summarize_non_streaming(payload: dict) -> str:
    response = _SESSION.post(
        OLLAMA_URL,
        json={**payload, "stream": False},
        timeout=(10, REQUEST_TIMEOUT_SECONDS),
    )
    response.raise_for_status()
    data = response.json()
    if "message" not in data or "content" not in data["message"]:
        raise RuntimeError(f"Unexpected Ollama response format: {data}")
    return data["message"]["content"].strip()


def summarize(stories: list[dict]) -> str:
    if MAX_SUMMARY_STORIES > 0:
        stories = stories[:MAX_SUMMARY_STORIES]

    log.info("Sending %d stories to Ollama (%s @ %s)...", len(stories), MODEL_NAME, OLLAMA_URL)
    payload = {
        "model": MODEL_NAME,
        "messages": [
            {
                "role": "system",
                "content": "You are an accurate news summarization assistant. Never fabricate facts."
            },
            {
                "role": "user",
                "content": build_prompt(stories)
            }
        ],
        "options": {
            "num_predict": NUM_PREDICT,
            "num_ctx": NUM_CTX,
            "temperature": 0.2,
        },
    }

    try:
        if STREAM_CHAT:
            summary = _summarize_streaming(payload)
        else:
            summary = _summarize_non_streaming(payload)
    except requests.exceptions.ReadTimeout as exc:
        raise RuntimeError(
            "Ollama summarization timed out. "
            "Try lower prompt size (NEWS_MAX_BODY_CHARS), lower output (OLLAMA_NUM_PREDICT), "
            "or higher timeout (OLLAMA_TIMEOUT_SECONDS)."
        ) from exc
    except requests.exceptions.ConnectionError as exc:
        raise RuntimeError(
            "Could not connect to Ollama. Make sure Ollama is running and OLLAMA_URL is correct."
        ) from exc
    log.info("Summarization complete.")
    return summary
