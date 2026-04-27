"""Configuration loading and validation."""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any

import yaml
from dotenv import load_dotenv


def find_project_root() -> Path:
    """Find project root by looking for config.yaml."""
    # Start from the script location and walk up
    current = Path(__file__).resolve().parent
    for _ in range(5):
        if (current / "config.yaml").exists():
            return current
        parent = current.parent
        if parent == current:
            break
        current = parent
    # Fallback: CWD
    cwd = Path.cwd()
    if (cwd / "config.yaml").exists():
        return cwd
    # Check if we're in src/
    if (cwd.parent / "config.yaml").exists():
        return cwd.parent
    return cwd


def load_config(config_path: str | None = None) -> dict[str, Any]:
    """Load configuration from YAML file and environment."""
    load_dotenv()

    root = find_project_root()

    if config_path is None:
        config_path = os.environ.get("NEWS_AGENT_CONFIG", str(root / "config.yaml"))

    path = Path(config_path)
    if not path.is_absolute():
        path = root / path

    if not path.exists():
        raise FileNotFoundError(f"Config file not found: {path}")

    with open(path) as f:
        config = yaml.safe_load(f)

    # Environment overrides
    if os.environ.get("OLLAMA_BASE_URL"):
        config["model"]["base_url"] = os.environ["OLLAMA_BASE_URL"]
    if os.environ.get("OLLAMA_MODEL"):
        config["model"]["name"] = os.environ["OLLAMA_MODEL"]
    if os.environ.get("NEWS_AGENT_DB"):
        config["paths"]["database"] = os.environ["NEWS_AGENT_DB"]

    # Resolve relative paths against project root
    for key in ("database", "reports", "logs", "debug"):
        p = Path(config["paths"][key])
        if not p.is_absolute():
            config["paths"][key] = str(root / p)

    # Store root for reference
    config["_project_root"] = str(root)

    return config
