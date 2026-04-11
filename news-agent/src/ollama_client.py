from __future__ import annotations

import json
import logging
import time
from typing import Any

import requests
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry

from settings import RuntimeSettings

log = logging.getLogger(__name__)

_RETRY = Retry(
    total=2,
    connect=2,
    read=0,
    backoff_factor=1,
    status_forcelist=[429, 500, 502, 503, 504],
    allowed_methods=["POST"],
    raise_on_status=False,
)

_HEALTH_PROBE = [
    {"role": "user", "content": 'Return only this exact JSON, nothing else: {"ok": true}'}
]


class OllamaClient:
    """Thin HTTP wrapper around the Ollama /api/chat endpoint."""

    def __init__(self, settings: RuntimeSettings) -> None:
        self.settings = settings
        self._session = self._make_session()

    # ── Public ────────────────────────────────────────────────────────────────

    def health_check(self) -> bool:
        """Send a minimal JSON probe before the main pipeline starts.

        Returns True if Ollama responds with any non-empty content.
        A False result means Ollama is down, the model is not loaded,
        or the context window is too small to return any tokens.
        """
        log.debug("Running Ollama health check against %s ...", self.settings.ollama_url)
        try:
            result = self.chat(_HEALTH_PROBE, num_predict=20, json_format=True)
            if result.strip():
                log.info("Ollama health check passed.")
                return True
            log.warning("Ollama health check: model returned empty output.")
            return False
        except Exception as exc:
            log.warning("Ollama health check failed: %s", exc)
            return False

    def chat(
        self,
        messages: list[dict[str, str]],
        *,
        num_predict: int,
        json_format: bool = False,
    ) -> str:
        payload: dict[str, Any] = {
            "model": self.settings.local_ai_model,
            "messages": messages,
            "options": {
                "num_predict": num_predict,
                "num_ctx": self.settings.num_ctx,
                "temperature": 0.1,
            },
        }
        if json_format:
            payload["format"] = "json"

        use_stream = self.settings.ollama_stream and not json_format

        try:
            if use_stream:
                return self._stream_chat(payload)
            return self._non_stream_chat(payload)
        except requests.exceptions.ReadTimeout as exc:
            raise RuntimeError(
                "Ollama timed out. Lower max_body_chars or the output budgets in runtime_settings.txt, "
                "or increase ollama_timeout_seconds."
            ) from exc
        except requests.exceptions.ConnectionError as exc:
            raise RuntimeError(
                "Could not connect to Ollama. Make sure Ollama is running and ollama_url is correct."
            ) from exc

    # ── Private ───────────────────────────────────────────────────────────────

    def _make_session(self) -> requests.Session:
        session = requests.Session()
        adapter = HTTPAdapter(max_retries=_RETRY)
        session.mount("http://", adapter)
        session.mount("https://", adapter)
        return session

    def _stream_chat(self, payload: dict[str, Any]) -> str:
        chunks: list[str] = []
        chars = 0
        last_progress = time.monotonic()
        first_token_logged = False

        with self._session.post(
            self.settings.ollama_url,
            json={**payload, "stream": True},
            timeout=(10, self.settings.ollama_timeout_seconds),
            stream=True,
        ) as response:
            self._raise_for_status_with_ollama_message(response)

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
                if now - last_progress >= self.settings.progress_log_seconds:
                    log.info("Ollama progress: %d characters generated...", chars)
                    last_progress = now

                if data.get("done"):
                    break

        return "".join(chunks).strip()

    def _non_stream_chat(self, payload: dict[str, Any]) -> str:
        response = self._session.post(
            self.settings.ollama_url,
            json={**payload, "stream": False},
            timeout=(10, self.settings.ollama_timeout_seconds),
        )
        self._raise_for_status_with_ollama_message(response)
        data = response.json()
        if "message" not in data or "content" not in data["message"]:
            raise RuntimeError(f"Unexpected Ollama response format: {data}")
        return data["message"]["content"].strip()

    def _raise_for_status_with_ollama_message(self, response: requests.Response) -> None:
        if response.ok:
            return

        message = None
        try:
            data = response.json()
            if isinstance(data, dict):
                message = data.get("error") or data.get("message")
        except ValueError:
            message = None

        if not message:
            message = response.text.strip() or f"HTTP {response.status_code}"

        raise RuntimeError(f"Ollama HTTP {response.status_code}: {message}")
