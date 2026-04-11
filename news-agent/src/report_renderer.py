from __future__ import annotations

from typing import Any

from contracts import ReviewedItem, RunManifest
from criteria import ReviewCriterion


def render_report_from_reviews(
    reviewed_items: list[ReviewedItem],
    criteria: list[ReviewCriterion],
    *,
    run_manifest: RunManifest,
    model_name: str = "local model",
) -> str:
    if not reviewed_items:
        return "No articles were reviewed in this run."

    grouped = _group_by_topic(reviewed_items)
    sorted_topics = sorted(grouped.values(), key=_topic_sort_key)
    counts = run_manifest.get("counts", {})

    lines = [
        "Run Health",
        f"- Status: {str(run_manifest.get('status', 'unknown')).upper()}",
        f"- Local model: {model_name}",
        (
            f"- Review mix: {int(counts.get('fresh_model_reviews', 0))} fresh model, "
            f"{int(counts.get('cached_reviews', 0))} cached, "
            f"{int(counts.get('heuristic_fallback_reviews', 0))} fallback."
        ),
        (
            f"- Coverage: {int(counts.get('collected_articles', 0))} collected, "
            f"{int(counts.get('reviewed_articles', 0))} reviewed, "
            f"{int(counts.get('worth_reviewing', 0))} marked worth review."
        ),
        (
            f"- Health: usable review ratio {counts.get('usable_review_ratio', 0)} "
            f"(target {run_manifest.get('min_usable_review_ratio', 0)}), "
            f"{int(counts.get('source_failures', 0))} source failure(s), "
            f"{int(counts.get('article_extraction_failures', 0))} extraction failure(s), "
            f"{int(counts.get('candidate_skips', 0))} skipped candidate(s)."
        ),
        "",
    ]

    if run_manifest.get("status") == "degraded":
        lines.append(
            "- Warning: this run is degraded because the usable review ratio fell below the configured threshold."
        )
        lines.append("")

    high_priority = [topic for topic in sorted_topics if topic["worth_reviewing"]]
    new_topics = [topic for topic in high_priority if topic["status"] == "new"]
    ongoing_topics = [topic for topic in high_priority if topic["status"] != "new"]
    excluded_topics = [topic for topic in sorted_topics if not topic["worth_reviewing"]]

    lines.extend(_render_topic_section("High Priority Topics", high_priority, criteria))
    lines.extend(_render_topic_section("New Topics", new_topics, criteria))
    lines.extend(_render_topic_section("Ongoing Updates", ongoing_topics, criteria))
    lines.extend(_render_excluded_section(excluded_topics))
    lines.extend(_render_source_health(run_manifest.get("source_health", [])))

    return "\n".join(line for line in lines if line is not None).strip()


def _render_topic_section(
    title: str,
    topics: list[dict[str, Any]],
    criteria: list[ReviewCriterion],
) -> list[str]:
    lines = [title]
    if not topics:
        lines.append("- None.")
        lines.append("")
        return lines

    for topic_data in topics:
        lines.extend(
            [
                f"Topic: {topic_data['name']}",
                f"- Classification: {topic_data['classification']}",
                f"- Priority: {topic_data['priority']}",
                f"- Why included: {topic_data['review_reason']}",
                f"- Source picture: {topic_data['source_picture']}",
                "- Key points:",
            ]
        )
        for point in topic_data["summaries"]:
            lines.append(f"  - {point}")

        lines.append("- Criteria signals:")
        for criterion_line in _criteria_summary_lines(topic_data["criteria_scores"], criteria):
            lines.append(f"  - {criterion_line}")

        lines.append("- Claims to verify:")
        if topic_data["claims_to_verify"]:
            for claim in topic_data["claims_to_verify"]:
                lines.append(f"  - {claim}")
        else:
            lines.append("  - None listed.")

        lines.append("- Coverage:")
        for link in topic_data["links"]:
            lines.append(f"  - {link}")
        lines.append("")

    return lines


def _render_excluded_section(excluded_topics: list[dict[str, Any]]) -> list[str]:
    lines = ["Excluded Items"]
    if not excluded_topics:
        lines.append("- None.")
        lines.append("")
        return lines

    for topic_data in excluded_topics:
        lines.append(
            f"- {topic_data['name']} ({topic_data['source_picture']}): {topic_data['review_reason']}"
        )
    lines.append("")
    return lines


def _render_source_health(source_health: list[dict[str, Any]]) -> list[str]:
    lines = ["Source Failures / Collection Issues"]
    noteworthy = [
        item
        for item in source_health
        if item.get("status") in {"failed", "partial"}
        or int(item.get("article_extraction_failures", 0)) > 0
        or int(item.get("candidate_skips", 0)) > 0
    ]
    if not noteworthy:
        lines.append("- None.")
        lines.append("")
        return lines

    for item in noteworthy:
        detail = (
            f"{item.get('source')} [{item.get('status')}] - "
            f"links={item.get('links_found', 0)}, "
            f"collected={item.get('stories_collected', 0)}, "
            f"skipped={item.get('candidate_skips', 0)}, "
            f"extraction_failures={item.get('article_extraction_failures', 0)}"
        )
        if item.get("homepage_error"):
            detail += f", homepage_error={item.get('homepage_error')}"
        lines.append(f"- {detail}")
    lines.append("")
    return lines


def _group_by_topic(reviewed_items: list[ReviewedItem]) -> dict[str, dict[str, Any]]:
    grouped: dict[str, dict[str, Any]] = {}
    for item in reviewed_items:
        story = item["story"]
        review = item["review"]
        topic = item.get("topic", {})
        topic_name = topic.get("name") or review.get("topic_hint") or story.get("title") or "Untitled topic"
        entry = grouped.setdefault(
            topic_name,
            {
                "name": topic_name,
                "status": item.get("topic_status", "unknown"),
                "worth_reviewing": False,
                "priority_rank": 99,
                "priority": "ignore",
                "review_reason": review.get("review_reason") or review.get("summary") or story.get("title", ""),
                "source_names": [],
                "summaries": [],
                "claims_to_verify": [],
                "links": [],
                "criteria_scores": {},
            },
        )

        entry["worth_reviewing"] = entry["worth_reviewing"] or review.get("worth_reviewing", False)
        if item.get("topic_status") == "new":
            entry["status"] = "new"
        elif entry["status"] != "new" and item.get("topic_status") == "existing":
            entry["status"] = "existing"

        current_rank = _priority_rank(review.get("priority", "ignore"))
        if current_rank < entry["priority_rank"]:
            entry["priority_rank"] = current_rank
            entry["priority"] = review.get("priority", "ignore")
            entry["review_reason"] = review.get("review_reason") or entry["review_reason"]

        source = story.get("source", "unknown")
        if source not in entry["source_names"]:
            entry["source_names"].append(source)

        summary = review.get("summary")
        if summary and summary not in entry["summaries"]:
            entry["summaries"].append(summary)

        for claim in review.get("claims_to_verify", []):
            if claim not in entry["claims_to_verify"]:
                entry["claims_to_verify"].append(claim)

        narrative = review.get("narrative_frame", "")
        narrative_tag = f" ({narrative})" if narrative and narrative != "unknown" else ""
        link_line = (
            f"{source} [{review.get('source_language', 'unknown')} | "
            f"{review.get('political_orientation', 'unknown')}]{narrative_tag} - "
            f"{story.get('title', '')} - {story.get('url', '')}"
        )
        if link_line not in entry["links"]:
            entry["links"].append(link_line)

        for criterion in review.get("criteria_scores", []):
            current = entry["criteria_scores"].get(criterion["criterion"])
            if current is None or criterion["score"] > current["score"]:
                entry["criteria_scores"][criterion["criterion"]] = criterion

    for entry in grouped.values():
        source_count = len(entry["source_names"])
        if source_count > 1:
            entry["classification"] = (
                "new corroborated multi-source topic"
                if entry["status"] == "new"
                else "corroborated multi-source topic"
            )
            entry["source_picture"] = f"multi-source; {', '.join(entry['source_names'])}"
        else:
            entry["classification"] = "new topic" if entry["status"] == "new" else "single-source item"
            entry["source_picture"] = f"single-source; {', '.join(entry['source_names'])}"
    return grouped


def _criteria_summary_lines(
    criteria_scores: dict[str, dict[str, Any]],
    criteria: list[ReviewCriterion],
) -> list[str]:
    lines = []
    for criterion in criteria:
        entry = criteria_scores.get(criterion.name)
        if entry is None:
            continue
        lines.append(f"{criterion.name}: {entry['score']}/10 - {entry['reason']}")
    return lines


def _priority_rank(priority: str) -> int:
    order = {"breaking": 0, "high": 1, "medium": 2, "low": 3, "ignore": 4}
    return order.get(priority, 5)


def _topic_sort_key(topic_data: dict[str, Any]) -> tuple:
    return (topic_data["priority_rank"], topic_data["name"].lower())
