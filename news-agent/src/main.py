import logging
import os
import sys
from pathlib import Path

from analyzer import render_report_from_reviews, review_story, synthesize_report
from memory import TopicMemory
from scraper import collect_stories
from utils import dedupe_stories
from writer import make_run_id, write_articles_artifact, write_run_artifacts, write_stage1_report

_LOG_DIR = Path(__file__).parent.parent / "logs"
_LOG_FILE = _LOG_DIR / "run.log"


def _setup_logging() -> None:
    _LOG_DIR.mkdir(exist_ok=True)
    fmt = "%(asctime)s %(levelname)-8s %(name)s: %(message)s"
    logging.basicConfig(
        level=logging.DEBUG,
        format=fmt,
        handlers=[
            logging.FileHandler(_LOG_FILE, encoding="utf-8"),
            logging.StreamHandler(sys.stdout),
        ],
    )
    # Keep console output at INFO so it stays readable
    logging.getLogger().handlers[1].setLevel(logging.INFO)


log = logging.getLogger(__name__)


def _source_priority(story: dict) -> int:
    try:
        return int(story.get("source_priority", 0))
    except (TypeError, ValueError):
        return 0


def _max_review_stories() -> int:
    return int(os.getenv("NEWS_MAX_REVIEW_STORIES", "5"))


def main():
    _setup_logging()
    log.info("=== Stage 1 News Agent started ===")
    run_id = make_run_id()

    log.info("[1/7] Collecting media articles...")
    print("[1/7] Collecting media articles...")
    stories = collect_stories()

    if not stories:
        log.error("No stories were extracted. Check selectors or site availability.")
        raise RuntimeError("No stories were extracted. Check selectors or site availability.")

    log.info("[2/7] Collected %d articles", len(stories))
    print(f"[2/7] Collected {len(stories)} articles")
    stories = dedupe_stories(stories)
    stories = sorted(stories, key=_source_priority, reverse=True)
    log.info("[3/7] %d articles after deduplication and source-priority ordering", len(stories))
    print(f"[3/7] {len(stories)} articles after deduplication and source-priority ordering")

    articles_path = write_articles_artifact(stories, run_id=run_id)
    log.info("Saved AI-readable article file: %s", articles_path)
    print(f"[4/7] Saved AI-readable article file: {articles_path}")

    max_review = _max_review_stories()
    stories_to_review = stories[:max_review] if max_review > 0 else stories
    log.info("[5/7] Reviewing %d articles with local model...", len(stories_to_review))
    print(f"[5/7] Reviewing {len(stories_to_review)} articles with local model...")

    memory = TopicMemory.load()
    reviewed_items = []
    for index, story in enumerate(stories_to_review, start=1):
        print(f"      Reviewing {index}/{len(stories_to_review)}: {story['title'][:90]}")
        review = review_story(story)
        item = {
            "story": story,
            "review": review,
            "topic_status": "excluded",
            "topic": {},
            "cross_check": {},
        }
        if review["worth_reviewing"]:
            attachment = memory.attach(story, review)
            item.update(attachment)
        reviewed_items.append(item)

    memory.save()

    artifacts = write_run_artifacts(stories, reviewed_items, run_id=run_id)

    log.info("[6/7] Synthesizing cross-source report...")
    print("[6/7] Synthesizing cross-source report...")
    try:
        report = synthesize_report(reviewed_items)
    except Exception as exc:
        log.warning("Model report synthesis failed; using structured fallback report: %s", exc)
        report = render_report_from_reviews(reviewed_items)

    log.info("[7/7] Writing report to Desktop...")
    print("[7/7] Writing report to Desktop...")
    outpath = write_stage1_report(report, reviewed_items, artifacts)
    log.info("Done. Saved to: %s", outpath)
    print(f"Done. Saved to: {outpath}")


if __name__ == "__main__":
    main()
