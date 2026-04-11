from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path

from settings import RuntimeSettings
from utils import project_root, safe_filename


def _timestamp() -> str:
    return datetime.now().strftime("%Y-%m-%d_%H-%M-%S")


def make_run_id() -> str:
    return _timestamp()


def _write_jsonl(path: Path, rows: list[dict]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8", newline="\n") as f:
        for row in rows:
            f.write(json.dumps(row, ensure_ascii=False))
            f.write("\n")


def write_articles_artifact(settings: RuntimeSettings, stories: list[dict], run_id: str | None = None) -> Path:
    run_id = run_id or _timestamp()
    date_part = run_id[:10]
    articles_path = settings.articles_dir / date_part / f"articles_{run_id}.jsonl"
    _write_jsonl(articles_path, stories)
    return articles_path


def _review_row(item: dict) -> dict:
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
        },
        "cross_check": item.get("cross_check"),
    }


def _run_manifest(
    settings: RuntimeSettings,
    stories: list[dict],
    reviewed_items: list[dict],
    artifacts: dict[str, Path],
    run_id: str,
) -> dict:
    worthy_count = sum(1 for item in reviewed_items if item.get("review", {}).get("worth_reviewing"))
    model_review_count = sum(1 for item in reviewed_items if item.get("review", {}).get("review_method") == "model")
    cached_review_count = sum(1 for item in reviewed_items if item.get("review", {}).get("review_method") == "cached")
    fallback_review_count = sum(1 for item in reviewed_items if item.get("review", {}).get("review_method") == "heuristic_fallback")
    usable_reviews = model_review_count + cached_review_count
    status = "degraded" if usable_reviews == 0 and len(reviewed_items) > 0 else "ok"
    return {
        "schema_version": 2,
        "status": status,
        "run_id": run_id,
        "generated_at": datetime.now().isoformat(timespec="seconds"),
        "settings_file": str(settings.path),
        "source_config_file": str(settings.source_config_path),
        "source_rules_file": str(settings.source_rules_path),
        "criteria_file": str(settings.criteria_path),
        "artifacts": {name: str(path) for name, path in artifacts.items()},
        "counts": {
            "collected_articles": len(stories),
            "reviewed_articles": len(reviewed_items),
            "worth_reviewing": worthy_count,
            "model_reviews": model_review_count,
            "cached_reviews": cached_review_count,
            "heuristic_fallback_reviews": fallback_review_count,
        },
        "reviewed_story_ids": [item["story"].get("id") for item in reviewed_items],
        "report_mode": settings.report_mode,
        "review_mode": settings.review_mode,
    }


def write_run_artifacts(
    settings: RuntimeSettings,
    stories: list[dict],
    reviewed_items: list[dict],
    articles_path: Path | None = None,
    run_id: str | None = None,
) -> dict[str, Path]:
    run_id = run_id or _timestamp()
    date_part = run_id[:10]
    articles_path = articles_path or settings.articles_dir / date_part / f"articles_{run_id}.jsonl"
    reviews_path = settings.reviews_dir / date_part / f"reviews_{run_id}.jsonl"
    combined_path = settings.runs_dir / date_part / f"run_{run_id}.json"

    if not articles_path.exists():
        _write_jsonl(articles_path, stories)
    _write_jsonl(reviews_path, [_review_row(item) for item in reviewed_items])

    combined_path.parent.mkdir(parents=True, exist_ok=True)
    artifacts = {
        "articles": articles_path,
        "reviews": reviews_path,
        "run": combined_path,
    }
    combined_path.write_text(
        json.dumps(_run_manifest(settings, stories, reviewed_items, artifacts, run_id), ensure_ascii=False, indent=2),
        encoding="utf-8",
    )

    return artifacts


def _format_sources(reviewed_items: list[dict]) -> list[str]:
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
        lines.append("")
    return lines


def write_stage1_report(
    settings: RuntimeSettings,
    report_text: str,
    reviewed_items: list[dict],
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


def write_digest(summary: str, stories: list[dict]) -> Path:
    desktop = project_root() / "reports"
    desktop.mkdir(parents=True, exist_ok=True)
    filename = f"news_digest_{datetime.now().strftime('%Y-%m-%d')}.txt"
    outpath = desktop / filename

    lines = [
        "Daily News Digest",
        f"Generated: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}",
        "",
        "=" * 80,
        "",
        summary,
        "",
        "=" * 80,
        "",
        "Sources Used",
        ""
    ]

    for story in stories:
        lines.append(f"- {story['source']}: {story['title']}")
        lines.append(f"  {story['url']}")
        lines.append("")

    outpath.write_text("\n".join(lines), encoding="utf-8")
    return outpath
