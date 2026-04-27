"""Watch mode scheduler."""

from __future__ import annotations

import logging
import time
from datetime import datetime, timezone

from .pipeline import run_pipeline

logger = logging.getLogger("news_agent.jobs.scheduler")


def run_watch(config: dict) -> None:
    """Run the pipeline in a loop every interval."""
    interval = config.get("scraping", {}).get("interval_minutes", 60)
    print(f"Watch mode: running every {interval} minutes. Press Ctrl+C to stop.\n")

    while True:
        try:
            now = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
            print(f"\n--- Run starting at {now} ---")
            run_pipeline(config)
        except KeyboardInterrupt:
            print("\nWatch mode stopped by user.")
            break
        except Exception as e:
            logger.error(f"Pipeline run failed: {e}")
            print(f"\nRun failed: {e}. Will retry in {interval} minutes.")

        try:
            time.sleep(interval * 60)
        except KeyboardInterrupt:
            print("\nWatch mode stopped by user.")
            break
