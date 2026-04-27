"""Ollama API client."""

from __future__ import annotations

import json
import logging
import time

import requests

logger = logging.getLogger("news_agent.analysis.ollama")


class OllamaClient:
    def __init__(self, base_url: str = "http://localhost:11434",
                 model: str = "gemma4:e4b",
                 timeout: int = 120,
                 max_retries: int = 3):
        self.base_url = base_url.rstrip("/")
        self.model = model
        self.timeout = timeout
        self.max_retries = max_retries

    def is_available(self) -> bool:
        """Check if Ollama is reachable."""
        try:
            resp = requests.get(f"{self.base_url}/api/tags", timeout=10)
            return resp.status_code == 200
        except Exception:
            return False

    def model_exists(self) -> bool:
        """Check if the configured model is available."""
        try:
            resp = requests.get(f"{self.base_url}/api/tags", timeout=10)
            if resp.status_code != 200:
                return False
            data = resp.json()
            models = [m.get("name", "") for m in data.get("models", [])]
            return any(self.model in m for m in models)
        except Exception:
            return False

    def generate(self, prompt: str, system: str = "",
                 temperature: float = 0.1) -> str | None:
        """Generate a completion from Ollama."""
        payload = {
            "model": self.model,
            "prompt": prompt,
            "stream": False,
            "options": {
                "temperature": temperature,
            },
        }
        if system:
            payload["system"] = system

        for attempt in range(1, self.max_retries + 1):
            try:
                resp = requests.post(
                    f"{self.base_url}/api/generate",
                    json=payload,
                    timeout=self.timeout,
                )
                resp.raise_for_status()
                data = resp.json()
                return data.get("response", "")
            except requests.exceptions.Timeout:
                logger.warning(f"Ollama timeout (attempt {attempt}/{self.max_retries})")
            except Exception as e:
                logger.warning(f"Ollama error (attempt {attempt}/{self.max_retries}): {e}")

            if attempt < self.max_retries:
                time.sleep(2 * attempt)

        return None

    def generate_json(self, prompt: str, system: str = "",
                      temperature: float = 0.1) -> dict | list | None:
        """Generate and parse JSON output from Ollama."""
        from ..utils.json_repair import safe_parse_json

        raw = self.generate(prompt, system, temperature)
        if raw is None:
            return None

        result = safe_parse_json(raw)
        if result is None:
            logger.warning("Failed to parse model JSON output")
            logger.debug(f"Raw output: {raw[:500]}")
        return result
