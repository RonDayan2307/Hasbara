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
from article_filter import ArticleFilter
from memory import TopicMemory
from report_renderer import render_report_from_reviews
from seen_urls import SeenUrlStore
from telemetry import IngestionTelemetry
from settings import load_runtime_settings
from scraper import iter_stories
from utils import is_duplicate_story, shorten_for_display
from writer import (
    build_run_manifest,
    make_run_id,
    write_articles_artifact,
    write_run_artifacts,
    write_stage1_report,
)

_LOG_DIR = Path(__file__).parent.parent / "logs"
_LOG_FILE = _LOG_DIR / "run.log"


def _setup_logging() -> None:
    _LOG_DIR.mkdir(exist_ok=True)
    fmt = "%(asctime)s %(levelname)-8s %(name)s: %(message)s"
    root_logger = logging.getLogger()
    root_logger.handlers.clear()
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


def main():
    settings = load_runtime_settings()
    _setup_logging()
    log.info("=== Stage 1 News Agent started ===")
    log.info("Using runtime settings: %s", settings.path)
    run_id = make_run_id()
    analyzer = LocalAiAnalyzer(settings)
    _COLLECTION_WINDOW_HOURS = 2

    # Step 0: health check — abort early if Ollama is not responding
    log.info("[0/7] Checking Ollama health (%s)...", settings.local_ai_model)
    print(f"[0/7] Checking Ollama health ({settings.local_ai_model})...")
    if not analyzer.health_check():
        log.error(
            "Ollama health check failed. The model returned invalid structured output or could not be reached."
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
    article_filter = ArticleFilter.load(settings.rejected_urls_path)
    seen_url_store = SeenUrlStore.load(settings.seen_urls_path)
    telemetry = IngestionTelemetry()
    stories = []
    reviewed_items = []

    log.info(
        "[2/7] Processing all articles from the last %d hours (seen: %d URLs already checked)...",
        _COLLECTION_WINDOW_HOURS,
        seen_url_store.count,
    )
    print(
        f"[2/7] Processing articles from the last {_COLLECTION_WINDOW_HOURS}h "
        f"({seen_url_store.count} URLs already checked)..."
    )
    for candidate in iter_stories(
        settings,
        telemetry=telemetry,
        window_hours=_COLLECTION_WINDOW_HOURS,
        seen_url_store=seen_url_store,
    ):
        if is_duplicate_story(candidate, stories):
            log.info("Skipping duplicate article: %s", candidate["title"])
            continue

        # -- Pre-processing filter: skip URLs previously rejected (avg < 3) --
        if article_filter.is_rejected(candidate["url"]):
            log.info(
                "Skipping previously rejected URL (avg < 3): %s", candidate["url"]
            )
            continue

        # -- Per-source context: log how many from this source were rejected --
        source_name = candidate.get("source", "unknown")
        rejected_from_source = article_filter.get_source_rejected_count(source_name)
        if rejected_from_source:
            log.info(
                "Source context: %s has %d previously rejected URL(s) on record.",
                source_name,
                rejected_from_source,
            )

        story = candidate
        stories.append(story)
        display_title = shorten_for_display(story["title"], max_length=98)
        print(f"      Processing #{len(stories)}: {display_title}")
        review = analyzer.review_story(story)

        # -- Post-review filter: classify by score thresholds --
        decision = article_filter.classify(review)
        article_filter.record(story, review, decision)

        if decision == "save" and not review["worth_reviewing"]:
            # Scores are high enough to override the standard worthy threshold
            review["worth_reviewing"] = True
            log.info(
                "Filter override: marking article worth reviewing (avg=%.1f, max=%.0f): %s",
                review["score_summary"]["average_score"],
                review["score_summary"]["max_score"],
                shorten_for_display(story["title"], max_length=68),
            )

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

    if not stories:
        log.error("No stories were extracted. Check selectors or site availability.")
        raise RuntimeError("No stories were extracted. Check selectors or site availability.")

    log.info("[3/7] Finished processing %d articles", len(stories))
    print(f"[3/7] Finished processing {len(stories)} articles")

    memory.save()
    analyzer.cache.save()
    article_filter.save()
    seen_url_store.save()

    articles_path = write_articles_artifact(settings, stories, run_id=run_id)
    log.info("[4/7] Saved AI-readable article file: %s", articles_path)
    print(f"[4/7] Saved AI-readable article file: {articles_path}")

    run_manifest = build_run_manifest(
        settings,
        stories,
        reviewed_items,
        run_id=run_id,
        source_health=telemetry.as_list(),
        cache_namespace=analyzer.cache_namespace,
        prompt_version=analyzer.prompt_version,
        normalization_version=analyzer.normalization_version,
    )
    artifacts, run_manifest = write_run_artifacts(
        settings,
        stories,
        reviewed_items,
        run_manifest,
        articles_path=articles_path,
        run_id=run_id,
    )
    log.info("[5/7] Saved review records and run manifest.")
    print("[5/7] Saved review records and run manifest.")

    log.info("[6/7] Synthesizing cross-source report...")
    print(f"[6/7] Synthesizing cross-source report ({settings.report_mode} mode)...")
    try:
        report = analyzer.synthesize_report(reviewed_items, run_manifest)
    except Exception as exc:
        log.warning("Report synthesis failed; using local structured report: %s", exc)
        report = render_report_from_reviews(
            reviewed_items,
            analyzer.criteria,
            run_manifest=run_manifest,
            model_name=settings.local_ai_model,
        )

    log.info("[7/7] Writing report to workspace...")
    print("[7/7] Writing report to workspace...")
    outpath = write_stage1_report(settings, report, reviewed_items, artifacts)
    log.info("Done. Saved to: %s", outpath)
    print(f"Done. Saved to: {outpath}")


if __name__ == "__main__":
    main()
