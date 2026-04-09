from __future__ import annotations

import json
import os
from datetime import datetime
from pathlib import Path

from utils import project_root, safe_filename


def _timestamp() -> str:
    return datetime.now().strftime("%Y-%m-%d_%H-%M-%S")


def make_run_id() -> str:
    return _timestamp()


def _desktop_output_dir() -> Path:
    configured = os.getenv("NEWS_OUTPUT_DIR")
    if configured:
        return Path(configured).expanduser()

    desktop = Path.home() / "Desktop"
    if desktop.exists():
        return desktop / "HasbaraReports"

    return project_root() / "reports"


def _write_jsonl(path: Path, rows: list[dict]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8", newline="\n") as f:
        for row in rows:
            f.write(json.dumps(row, ensure_ascii=False))
            f.write("\n")


def write_articles_artifact(stories: list[dict], run_id: str | None = None) -> Path:
    run_id = run_id or _timestamp()
    date_part = run_id[:10]
    data_dir = project_root() / "data"
    articles_path = data_dir / "articles" / date_part / f"articles_{run_id}.jsonl"
    _write_jsonl(articles_path, stories)
    return articles_path


def write_run_artifacts(stories: list[dict], reviewed_items: list[dict], run_id: str | None = None) -> dict[str, Path]:
    run_id = run_id or _timestamp()
    date_part = run_id[:10]
    data_dir = project_root() / "data"

    articles_path = data_dir / "articles" / date_part / f"articles_{run_id}.jsonl"
    reviews_path = data_dir / "reviews" / date_part / f"reviews_{run_id}.jsonl"
    combined_path = data_dir / "runs" / date_part / f"run_{run_id}.json"

    _write_jsonl(articles_path, stories)
    _write_jsonl(
        reviews_path,
        [
            {
                "story": item.get("story"),
                "review": item.get("review"),
                "topic_status": item.get("topic_status"),
                "topic": {
                    "id": item.get("topic", {}).get("id"),
                    "name": item.get("topic", {}).get("name"),
                    "window_start": item.get("topic", {}).get("window_start"),
                    "window_end": item.get("topic", {}).get("window_end"),
                    "source_count": item.get("topic", {}).get("source_count"),
                    "item_count": item.get("topic", {}).get("item_count"),
                },
                "cross_check": item.get("cross_check"),
            }
            for item in reviewed_items
        ],
    )

    combined_path.parent.mkdir(parents=True, exist_ok=True)
    combined_path.write_text(
        json.dumps(
            {
                "schema_version": 1,
                "run_id": run_id,
                "stories": stories,
                "reviewed_items": reviewed_items,
            },
            ensure_ascii=False,
            indent=2,
        ),
        encoding="utf-8",
    )

    return {
        "articles": articles_path,
        "reviews": reviews_path,
        "run": combined_path,
    }


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


def write_stage1_report(report_text: str, reviewed_items: list[dict], artifacts: dict[str, Path]) -> Path:
    outdir = _desktop_output_dir()
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
        f"- Topic memory: {project_root() / 'data' / 'topics.json'}",
        "",
        "## Sources",
        "",
        *_format_sources(reviewed_items),
    ]

    outpath.write_text("\n".join(lines), encoding="utf-8", newline="\n")
    return outpath


def write_digest(summary: str, stories: list[dict]) -> Path:
    desktop = _desktop_output_dir()
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
