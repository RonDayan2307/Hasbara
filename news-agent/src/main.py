import warnings
import logging
import sys
from pathlib import Path

warnings.filterwarnings(
    "ignore",
    message=r"urllib3 v2 only supports OpenSSL 1\.1\.1\+",
    module=r"urllib3",
)

from analyzer import LocalAiAnalyzer
from memory import TopicMemory
from report_renderer import render_report_from_reviews
from settings import load_runtime_settings
from scraper import iter_stories
from utils import is_duplicate_story
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


def main():
    settings = load_runtime_settings()
    _setup_logging()
    log.info("=== Stage 1 News Agent started ===")
    log.info("Using runtime settings: %s", settings.path)
    run_id = make_run_id()
    analyzer = LocalAiAnalyzer(settings)
    max_review = settings.max_review_stories

    # Step 0: health check — abort early if Ollama is not responding
    log.info("[0/7] Checking Ollama health (%s)...", settings.local_ai_model)
    print(f"[0/7] Checking Ollama health ({settings.local_ai_model})...")
    if not analyzer.health_check():
        log.error(
            "Ollama health check failed. The model returned empty output or could not be reached."
        )
        print()
        print("[ERROR] Ollama health check failed. Check that:")
        print("        1. Ollama is running:               ollama serve")
        print(f"        2. Model is pulled:                 ollama pull {settings.local_ai_model}")
        print(f"        3. ollama_url is correct:           {settings.ollama_url}")
        print(f"        4. num_ctx is not too large:        current = {settings.num_ctx}")
        print()
        sys.exit(1)

    log.info("[1/7] Collecting article links...")
    print("[1/7] Collecting article links...")
    memory = TopicMemory.load(settings)
    stories = []
    reviewed_items = []

    log.info("[2/7] Processing up to %d articles one by one...", max_review)
    print(f"[2/7] Processing up to {max_review} articles one by one...")
    for candidate in iter_stories(settings):
        if is_duplicate_story(candidate, stories):
            log.info("Skipping duplicate article: %s", candidate["title"])
            continue

        story = candidate
        stories.append(story)
        print(f"      Processing {len(stories)}/{max_review}: {story['title'][:90]}")
        review = analyzer.review_story(story)
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

        if max_review > 0 and len(stories) >= max_review:
            break

    if not stories:
        log.error("No stories were extracted. Check selectors or site availability.")
        raise RuntimeError("No stories were extracted. Check selectors or site availability.")

    stories = sorted(stories, key=_source_priority, reverse=True)
    log.info("[3/7] Finished processing %d articles", len(stories))
    print(f"[3/7] Finished processing {len(stories)} articles")

    memory.save()
    analyzer.cache.save()

    articles_path = write_articles_artifact(settings, stories, run_id=run_id)
    log.info("[4/7] Saved AI-readable article file: %s", articles_path)
    print(f"[4/7] Saved AI-readable article file: {articles_path}")

    artifacts = write_run_artifacts(settings, stories, reviewed_items, articles_path=articles_path, run_id=run_id)
    log.info("[5/7] Saved review records and run manifest.")
    print("[5/7] Saved review records and run manifest.")

    log.info("[6/7] Synthesizing cross-source report...")
    print(f"[6/7] Synthesizing cross-source report ({settings.report_mode} mode)...")
    try:
        report = analyzer.synthesize_report(reviewed_items)
    except Exception as exc:
        log.warning("Report synthesis failed; using local structured report: %s", exc)
        report = render_report_from_reviews(
            reviewed_items, analyzer.criteria, model_name=settings.local_ai_model
        )

    log.info("[7/7] Writing report to workspace...")
    print("[7/7] Writing report to workspace...")
    outpath = write_stage1_report(settings, report, reviewed_items, artifacts)
    log.info("Done. Saved to: %s", outpath)
    print(f"Done. Saved to: {outpath}")


if __name__ == "__main__":
    main()
