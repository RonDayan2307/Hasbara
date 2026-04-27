"""Terminal alert display."""

from __future__ import annotations

import sys


RESET = "\033[0m"
RED = "\033[91m"
YELLOW = "\033[93m"
BOLD = "\033[1m"
CYAN = "\033[96m"


def print_alerts(alerts: list[dict]) -> None:
    """Print alert summaries to terminal."""
    if not alerts:
        print(f"\n{CYAN}No high-priority alerts this run.{RESET}\n")
        return

    print(f"\n{RED}{BOLD}{'=' * 60}")
    print(f"  ALERTS ({len(alerts)} high-priority items)")
    print(f"{'=' * 60}{RESET}\n")

    for alert in alerts:
        score = alert.get("risk_score", 0)
        color = RED if score >= 9 else YELLOW

        print(f"{color}{BOLD}  [{score:.1f}] {alert.get('headline', alert.get('topic_name', 'Alert'))}{RESET}")
        print(f"    Sources: {alert.get('source_count', 0)} | Primary: {', '.join(alert.get('primary_sources', []))}")
        urls = alert.get("urls", [])
        if urls:
            print(f"    URL: {urls[0]}")
        print(f"    Reason: {alert.get('reason', '')}")
        print()

    print(f"{RED}{'=' * 60}{RESET}\n")


def print_run_summary(manifest: dict) -> None:
    """Print a brief run summary."""
    status = manifest.get("status", "unknown")
    color = CYAN if status == "healthy" else YELLOW

    print(f"\n{color}{BOLD}Run complete: {status}{RESET}")
    print(f"  Sources checked: {manifest.get('sources_checked', 0)}")
    print(f"  Articles collected: {manifest.get('articles_collected', 0)}")
    print(f"  Articles scored: {manifest.get('articles_scored', 0)}")
    print(f"  Topics found: {manifest.get('topics_found', 0)}")
    print(f"  Alerts raised: {manifest.get('alerts_raised', 0)}")

    if manifest.get("sources_failed", 0) > 0:
        print(f"  {YELLOW}Sources failed: {manifest['sources_failed']}{RESET}")
    if manifest.get("model_failures", 0) > 0:
        print(f"  {YELLOW}Model failures: {manifest['model_failures']}{RESET}")
    print()
