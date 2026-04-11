from __future__ import annotations

from typing import Any

from criteria import ReviewCriterion


def render_report_from_reviews(
    reviewed_items: list[dict[str, Any]],
    criteria: list[ReviewCriterion],
    model_name: str = "local model",
) -> str:
    if not reviewed_items:
        return "No articles were reviewed in this run."

    grouped = _group_by_topic(reviewed_items)
    sorted_topics = sorted(grouped.values(), key=_topic_sort_key)

    model_review_count = sum(
        1 for item in reviewed_items if item["review"].get("review_method") == "model"
    )
    fallback_review_count = sum(
        1 for item in reviewed_items if item["review"].get("review_method") == "heuristic_fallback"
    )
    cached_review_count = sum(
        1 for item in reviewed_items if item["review"].get("review_method") == "cached"
    )
    worthy_count = sum(1 for item in reviewed_items if item["review"].get("worth_reviewing"))

    lines = [
        "Executive Summary",
        "- This report was rendered locally for faster turnaround.",
        (
            f"- Review mix: {model_review_count} model review(s), "
            f"{cached_review_count} cached review(s), "
            f"{fallback_review_count} heuristic fallback review(s)."
        ),
        f"- {worthy_count} of {len(reviewed_items)} reviewed articles were marked worth human review.",
        "",
        "Priority Topics",
    ]

    if fallback_review_count:
        if fallback_review_count == len(reviewed_items):
            lines.insert(
                3,
                "- Warning: every reviewed item fell back to heuristics because the local model returned no usable structured output.",
            )
        else:
            lines.insert(
                3,
                f"- Warning: {fallback_review_count} reviewed item(s) used heuristic fallback.",
            )

    for topic_data in sorted_topics:
        if not topic_data["worth_reviewing"]:
            continue

        lines.extend(
            [
                f"Topic: {topic_data['name']}",
                f"Status: {topic_data['status']}",
                f"Why it matters: {topic_data['review_reason']}",
                f"Source picture: {topic_data['source_picture']}",
                "Key points:",
            ]
        )
        for point in topic_data["summaries"]:
            lines.append(f"- {point}")

        lines.append("Criteria picture:")
        for criterion_line in _criteria_summary_lines(topic_data["criteria_scores"], criteria):
            lines.append(f"- {criterion_line}")

        lines.append("Claims to verify:")
        if topic_data["claims_to_verify"]:
            for claim in topic_data["claims_to_verify"]:
                lines.append(f"- {claim}")
        else:
            lines.append("- None listed.")

        lines.append("Links:")
        for link in topic_data["links"]:
            lines.append(f"- {link}")
        lines.append("")

    excluded = [topic for topic in sorted_topics if not topic["worth_reviewing"]]
    if excluded:
        lines.append("Lower Priority / Excluded")
        for topic_data in excluded:
            lines.append(f"- {topic_data['name']}: {topic_data['review_reason']}")

    return "\n".join(lines).strip()


def _group_by_topic(reviewed_items: list[dict[str, Any]]) -> dict[str, dict[str, Any]]:
    grouped: dict[str, dict[str, Any]] = {}
    for item in reviewed_items:
        story = item["story"]
        review = item["review"]
        topic = item.get("topic", {})
        topic_name = (
            topic.get("name")
            or review.get("topic_hint")
            or story.get("title")
            or "Untitled topic"
        )
        entry = grouped.setdefault(
            topic_name,
            {
                "name": topic_name,
                "status": item.get("topic_status", "unknown"),
                "worth_reviewing": False,
                "priority_rank": 99,
                "review_reason": (
                    review.get("review_reason") or review.get("summary") or story.get("title", "")
                ),
                "source_names": [],
                "summaries": [],
                "claims_to_verify": [],
                "links": [],
                "criteria_scores": {},
            },
        )

        entry["worth_reviewing"] = entry["worth_reviewing"] or review.get("worth_reviewing", False)
        entry["status"] = (
            "existing"
            if entry["status"] == "existing" or item.get("topic_status") == "existing"
            else entry["status"]
        )
        entry["priority_rank"] = min(
            entry["priority_rank"], _priority_rank(review.get("priority", "ignore"))
        )

        source = story.get("source", "unknown")
        if source not in entry["source_names"]:
            entry["source_names"].append(source)

        summary = review.get("summary")
        if summary and summary not in entry["summaries"]:
            entry["summaries"].append(summary)

        for claim in review.get("claims_to_verify", []):
            if claim not in entry["claims_to_verify"]:
                entry["claims_to_verify"].append(claim)

        link_line = f"{source} - {story.get('title', '')} - {story.get('url', '')}"
        if link_line not in entry["links"]:
            entry["links"].append(link_line)

        for criterion in review.get("criteria_scores", []):
            current = entry["criteria_scores"].get(criterion["criterion"])
            if current is None or criterion["score"] > current["score"]:
                entry["criteria_scores"][criterion["criterion"]] = criterion

    for entry in grouped.values():
        source_count = len(entry["source_names"])
        entry["source_picture"] = (
            f"multi-source; {', '.join(entry['source_names'])}"
            if source_count > 1
            else f"single-source; {', '.join(entry['source_names'])}"
        )
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
