"""Logging configuration."""

from __future__ import annotations

import logging
import sys
from pathlib import Path


def setup_logging(log_dir: str, debug: bool = False) -> logging.Logger:
    """Configure logging to file and stderr."""
    Path(log_dir).mkdir(parents=True, exist_ok=True)
    log_file = Path(log_dir) / "news_agent.log"

    level = logging.DEBUG if debug else logging.INFO

    root_logger = logging.getLogger("news_agent")
    root_logger.setLevel(level)

    # Clear existing handlers
    root_logger.handlers.clear()

    fmt = logging.Formatter("%(asctime)s [%(levelname)s] %(name)s: %(message)s")

    fh = logging.FileHandler(log_file, encoding="utf-8")
    fh.setLevel(level)
    fh.setFormatter(fmt)
    root_logger.addHandler(fh)

    sh = logging.StreamHandler(sys.stderr)
    sh.setLevel(level)
    sh.setFormatter(fmt)
    root_logger.addHandler(sh)

    return root_logger
