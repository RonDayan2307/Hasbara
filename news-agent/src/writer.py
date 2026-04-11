from __future__ import annotations

import json
from copy import deepcopy
from datetime import datetime
from pathlib import Path
from typing import Any

from contracts import ReviewedItem, RunManifest, Story
from settings import RuntimeSettings
from utils import safe_filename


def _timestamp() -> str:
    return datetime.now().strftime("%Y-%m-%d_%H-%M-%S")


def make_run_id() -> str:
    return _timestamp()


def _write_jsonl(path: Path, rows: list[dict]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8", newline="\n") as handle:
        for row in rows:
            handle.write(json.dumps(row, ensure_ascii=False))
            handle.write("\n")


def write_articles_artifact(
    settings: RuntimeSettings,
    stories: list[Story],
    run_id: str | None = None,
) -> Path:
    run_id = run_id or _timestamp()
    date_part = run_id[:10]
    articles_path = settings.articles_dir / date_part / f"articles_{run_id}.jsonl"
    _write_jsonl(articles_path, stories)
    return articles_path


def build_run_manifest(
    settings: RuntimeSettings,
    stories: list[Story],
    reviewed_items: list[ReviewedItem],
    *,
    run_id: str,
    source_health: list[dict[str, Any]],
    cache_namespace: str,
    prompt_version: str,
    normalization_version: str,
    artifacts: dict[str, str] | None = None,
) -> RunManifest:
    worthy_count = sum(1 for item in reviewed_items if item.get("review", {}).get("worth_reviewing"))
    fresh_model_reviews = sum(
        1 for item in reviewed_items if item.get("review", {}).get("review_method") == "model"
    )
    cached_reviews = sum(
        1 for item in reviewed_items if item.get("review", {}).get("review_method") == "cached"
    )
    fallback_reviews = sum(
        1
        for item in reviewed_items
        if item.get("review", {}).get("review_method") == "heuristic_fallback"
    )
    reviewed_count = len(reviewed_items)
    usable_reviews = fresh_model_reviews + cached_reviews
    usable_review_ratio = round((usable_reviews / reviewed_count), 3) if reviewed_count else 0.0
    source_failures = sum(1 for item in source_health if item.get("status") == "failed")
    article_extraction_failures = sum(
        int(item.get("article_extraction_failures", 0)) for item in source_health
    )
    candidate_skips = sum(int(item.get("candidate_skips", 0)) for item in source_health)
    status = "ok"
    if reviewed_count == 0 or usable_review_ratio < settings.min_usable_review_ratio:
        status = "degraded"

    return {
        "schema_version": 3,
        "status": status,
        "run_id": run_id,
        "generated_at": datetime.now().isoformat(timespec="seconds"),
        "settings_file": str(settings.path),
        "source_config_file": str(settings.source_config_path),
        "source_rules_file": str(settings.source_rules_path),
        "criteria_file": str(settings.criteria_path),
        "artifacts": artifacts or {},
        "counts": {
            "collected_articles": len(stories),
            "reviewed_articles": reviewed_count,
            "worth_reviewing": worthy_count,
            "fresh_model_reviews": fresh_model_reviews,
            "cached_reviews": cached_reviews,
            "heuristic_fallback_reviews": fallback_reviews,
            "usable_reviews": usable_reviews,
            "usable_review_ratio": usable_review_ratio,
            "source_failures": source_failures,
            "article_extraction_failures": article_extraction_failures,
            "candidate_skips": candidate_skips,
        },
        "source_health": source_health,
        "reviewed_story_ids": [item["story"].get("id") for item in reviewed_items],
        "report_mode": settings.report_mode,
        "local_ai_model": settings.local_ai_model,
        "min_usable_review_ratio": settings.min_usable_review_ratio,
        "cache_namespace": cache_namespace,
        "prompt_version": prompt_version,
        "normalization_version": normalization_version,
    }


def _review_row(item: ReviewedItem) -> dict:
    review = item["review"]
    topic = item.get("topic", {})
    return {
        "story_id": review.get("story_id"),
        "review": review,
        "topic_status": item.get("topic_status"),
        "topic": {
            "id": topic.get("id"),
            "name": topic.get("name"),
            "window_start": topic.get("window_start"),
            "window_end": topic.get("window_end"),
            "source_count": topic.get("source_count"),
            "item_count": topic.get("item_count"),
            "highest_priority": topic.get("highest_priority"),
        },
        "cross_check": item.get("cross_check"),
    }


def write_run_artifacts(
    settings: RuntimeSettings,
    stories: list[Story],
    reviewed_items: list[ReviewedItem],
    run_manifest: RunManifest,
    *,
    articles_path: Path | None = None,
    run_id: str | None = None,
) -> tuple[dict[str, Path], RunManifest]:
    run_id = run_id or _timestamp()
    date_part = run_id[:10]
    articles_path = articles_path or settings.articles_dir / date_part / f"articles_{run_id}.jsonl"
    reviews_path = settings.reviews_dir / date_part / f"reviews_{run_id}.jsonl"
    manifest_path = settings.runs_dir / date_part / f"run_{run_id}.json"

    if not articles_path.exists():
        _write_jsonl(articles_path, stories)
    _write_jsonl(reviews_path, [_review_row(item) for item in reviewed_items])

    artifacts = {
        "articles": articles_path,
        "reviews": reviews_path,
        "run": manifest_path,
    }
    manifest_to_write = deepcopy(run_manifest)
    manifest_to_write["artifacts"] = {name: str(path) for name, path in artifacts.items()}

    manifest_path.parent.mkdir(parents=True, exist_ok=True)
    manifest_path.write_text(
        json.dumps(manifest_to_write, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    return artifacts, manifest_to_write


def _format_sources(reviewed_items: list[ReviewedItem]) -> list[str]:
    lines = []
    for item in reviewed_items:
        story = item["story"]
        review = item["review"]
        topic = item.get("topic", {})
        lines.append(
            f"- [{review.get('priority', 'unknown').upper()}] {story.get('source', 'unknown')}: "
            f"{story.get('title', '')}"
        )
        lines.append(f"  URL: {story.get('url', '')}")
        lines.append(
            f"  Topic: {topic.get('name') or review.get('topic_hint')} "
            f"({item.get('topic_status', 'unknown')})"
        )
        lines.append(
            f"  Language: {review.get('source_language', 'unknown')} | "
            f"Orientation: {review.get('political_orientation', 'unknown')}"
        )
        lines.append(
            f"  Review method: {review.get('review_method', 'unknown')} | "
            f"Quality: {review.get('review_quality', 'unknown')}"
        )
        lines.append(f"  Reason: {review.get('review_reason', '')}")
        lines.append("")
    return lines


def write_stage1_report(
    settings: RuntimeSettings,
    report_text: str,
    reviewed_items: list[ReviewedItem],
    artifacts: dict[str, Path],
) -> Path:
    outdir = settings.report_output_dir()
    outdir.mkdir(parents=True, exist_ok=True)
    filename = f"{safe_filename(f'hasbara stage1 report {_timestamp()}', max_length=120)}.md"
    outpath = outdir / filename

    lines = [
        "# Stage 1 Public Diplomacy Monitoring Report",
        "",
        f"Generated: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}",
        "",
        "## Report",
        "",
        report_text,
        "",
        "## Artifacts",
        "",
        f"- AI-readable articles: {artifacts['articles']}",
        f"- AI-readable reviews: {artifacts['reviews']}",
        f"- Full run JSON: {artifacts['run']}",
        f"- Topic memory: {settings.topics_path}",
        f"- Runtime settings: {settings.path}",
        f"- Review criteria: {settings.criteria_path}",
        f"- Source list: {settings.source_config_path}",
        f"- Source rules: {settings.source_rules_path}",
        "",
        "## Sources",
        "",
        *_format_sources(reviewed_items),
    ]

    outpath.write_text("\n".join(lines), encoding="utf-8")
    return outpath
