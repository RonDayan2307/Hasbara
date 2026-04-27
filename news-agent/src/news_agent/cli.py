"""CLI interface."""

from __future__ import annotations

import argparse
import sys

from .config import load_config
from .logging_setup import setup_logging


def main() -> int:
    parser = argparse.ArgumentParser(
        prog="news-agent",
        description="Local news monitoring agent for reputational-risk awareness.",
    )
    parser.add_argument("--config", "-c", help="Path to config.yaml")
    parser.add_argument("--debug", action="store_true", help="Enable debug logging")

    subparsers = parser.add_subparsers(dest="command")

    # run (default)
    sub_run = subparsers.add_parser("run", help="Run the pipeline once")

    # watch
    sub_watch = subparsers.add_parser("watch", help="Run continuously every hour")

    # doctor
    sub_doctor = subparsers.add_parser("doctor", help="Check system health")

    # cleanup
    sub_cleanup = subparsers.add_parser("cleanup", help="Run data retention cleanup")

    # report
    sub_report = subparsers.add_parser("report", help="Show latest report path")

    # sources
    sub_sources = subparsers.add_parser("sources", help="List configured sources")

    # alerts
    sub_alerts = subparsers.add_parser("alerts", help="Show recent alerts")

    # Also support --watch flag
    parser.add_argument("--watch", action="store_true", help="Run in watch mode")

    args = parser.parse_args()

    # Load config
    try:
        config = load_config(args.config)
    except Exception as e:
        print(f"Error loading config: {e}", file=sys.stderr)
        return 1

    # Setup logging
    setup_logging(config["paths"]["logs"], debug=args.debug)

    # Determine command
    command = args.command
    if not command:
        if args.watch:
            command = "watch"
        else:
            command = "run"

    if command == "doctor":
        from .jobs.doctor import run_doctor
        ok = run_doctor(args.config)
        return 0 if ok else 1

    elif command == "run":
        from .jobs.pipeline import run_pipeline
        manifest = run_pipeline(config)
        return 0 if manifest.status != "failed" else 1

    elif command == "watch":
        from .jobs.scheduler import run_watch
        run_watch(config)
        return 0

    elif command == "cleanup":
        from .jobs.cleanup import run_cleanup
        run_cleanup(config)
        return 0

    elif command == "report":
        from pathlib import Path
        latest = Path(config["paths"]["reports"]) / "latest.md"
        if latest.exists():
            print(f"Latest report: {latest}")
            print(latest.read_text(encoding="utf-8")[:2000])
        else:
            print("No reports generated yet. Run the pipeline first.")
        return 0

    elif command == "sources":
        from .sources.source_config import load_sources
        sources = load_sources(config)
        print(f"\nConfigured sources ({len(sources)}):\n")
        for s in sources:
            status = "enabled" if s.enabled else "disabled"
            rss = "RSS" if s.rss_url else "homepage"
            print(f"  [{status}] {s.name} ({s.region}) - {rss} - priority {s.priority}")
        print()
        return 0

    elif command == "alerts":
        from .db.connection import get_connection
        from .db.schema import init_schema
        from .db.repositories import AlertRepo, RunRepo
        import json

        conn = get_connection(config["paths"]["database"])
        init_schema(conn)
        run_repo = RunRepo(conn)
        alert_repo = AlertRepo(conn)

        last_run = run_repo.get_latest()
        if not last_run:
            print("No runs yet.")
            return 0

        alerts = alert_repo.get_by_run(last_run["id"])
        if not alerts:
            print("No alerts from last run.")
            return 0

        from .alerts.terminal import print_alerts
        print_alerts(alerts)
        conn.close()
        return 0

    return 0
