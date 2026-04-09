import logging
import sys
from pathlib import Path

from scraper import collect_stories
from summarizer import summarize
from utils import dedupe_stories
from writer import write_digest

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


def main():
    _setup_logging()
    log.info("=== News Agent started ===")

    log.info("[1/4] Collecting stories...")
    print("[1/4] Collecting stories...")
    stories = collect_stories()

    if not stories:
        log.error("No stories were extracted. Check selectors or site availability.")
        raise RuntimeError("No stories were extracted. Check selectors or site availability.")

    log.info("[2/4] Collected %d stories", len(stories))
    print(f"[2/4] Collected {len(stories)} stories")
    stories = dedupe_stories(stories)
    log.info("[3/4] %d stories after deduplication", len(stories))
    print(f"[3/4] {len(stories)} stories after deduplication")

    summary = summarize(stories)

    log.info("[4/4] Writing digest to Desktop...")
    print("[4/4] Writing digest to Desktop...")
    outpath = write_digest(summary, stories)
    log.info("Done. Saved to: %s", outpath)
    print(f"Done. Saved to: {outpath}")


if __name__ == "__main__":
    main()
