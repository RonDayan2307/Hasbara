"""Markdown report generation."""

from __future__ import annotations

import json
import logging
from datetime import datetime, timezone
from pathlib import Path

from ..topics.lifecycle import severity_label

logger = logging.getLogger("news_agent.reports.markdown")


def render_report(
    run_id: str,
    status: str,
    topics: list[dict],
    alerts: list[dict],
    changes: list[str],
    source_health: list[dict],
    output_dir: str,
) -> str:
    """Generate the full markdown report and write to file."""
    Path(output_dir).mkdir(parents=True, exist_ok=True)

    now = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
    timestamp_file = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M")
    filename = f"report_{timestamp_file}.md"
    filepath = Path(output_dir) / filename

    lines = []
    lines.append("# Hasbara News Agent Report")
    lines.append(f"Generated: {now}")
    lines.append(f"Run status: {status}")
    lines.append(f"Run ID: {run_id}")
    lines.append("")

    # What changed since last run
    lines.append("## What changed since last run")
    if changes:
        for change in changes:
            lines.append(f"- {change}")
    else:
        lines.append("- No significant changes detected.")
    lines.append("")

    # Source health summary
    failed = [s for s in source_health if s.get("status") != "ok"]
    if failed:
        lines.append("### Source issues")
        for s in failed:
            lines.append(f"- {s['source_name']}: {s.get('error_message', 'unknown error')}")
        lines.append("")

    # Terminal Alert Bucket
    if alerts:
        lines.append("## Terminal Alert Bucket")
        lines.append("")
        for alert in alerts:
            lines.append(f"### {alert.get('headline', alert.get('topic_name', 'Alert'))}")
            lines.append(f"- Risk score: {alert.get('risk_score', 0):.1f}")
            lines.append(f"- Sources: {alert.get('source_count', 0)}")
            primary = alert.get("primary_sources", [])
            if primary:
                lines.append(f"- Primary: {', '.join(primary)}")
            urls = alert.get("urls", [])
            for u in urls[:3]:
                lines.append(f"- URL: {u}")
            lines.append(f"- Reason: {alert.get('reason', '')}")
            lines.append("")

    # Topics
    for topic in sorted(topics, key=lambda t: t.get("final_score", 0), reverse=True):
        score = topic.get("final_score", 0)
        if score < 6.0:
            continue

        severity = severity_label(score)
        lifecycle = topic.get("lifecycle", "emerging")
        labels = topic.get("labels", [])
        sources = topic.get("source_names", [])
        name = topic.get("name", "Unknown Topic")

        lines.append(f"## Topic: {name}")
        lines.append(f"Severity: {severity}")
        lines.append(f"Lifecycle: {lifecycle.capitalize()}")
        lines.append(f"Final score: {score:.1f}")
        if labels:
            lines.append(f"Primary labels: {', '.join(labels)}")
        if sources:
            lines.append(f"Sources: {', '.join(sources)}")
        lines.append("")

        # Summary
        summary = topic.get("summary", "")
        if summary:
            lines.append("### Summary")
            lines.append(summary)
            lines.append("")

        # Why it matters
        why = topic.get("why_it_matters", "")
        if why:
            lines.append("### Why it matters")
            lines.append(why)
            lines.append("")

        # Claims
        claims = topic.get("claims", [])
        if claims:
            lines.append("### Key claims to verify")
            for claim in claims:
                lines.append(f"- Claim: {claim.get('claim_text', '')}")
                lines.append(f"  - Source URL: {claim.get('source_url', '')}")
                lines.append(f"  - Status: {claim.get('status', 'needs_human_verification')}")
            lines.append("")

        # Source comparison
        comparison = topic.get("source_comparison", {})
        comp_summary = comparison.get("comparison_summary", "")
        if comp_summary and comp_summary != "Source comparison unavailable.":
            lines.append("### Source comparison")
            lines.append(comp_summary)
            lines.append("")

        # Recommended response
        response = topic.get("recommended_response", "")
        if response:
            lines.append("### Recommended response")
            lines.append(response)
            lines.append("")

    content = "\n".join(lines)
    filepath.write_text(content, encoding="utf-8")
    logger.info(f"Report written to {filepath}")

    # Also write/overwrite latest.md symlink-like file
    latest = Path(output_dir) / "latest.md"
    latest.write_text(content, encoding="utf-8")

    return str(filepath)
