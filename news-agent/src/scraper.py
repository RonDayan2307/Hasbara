import json
import logging
import os
from urllib.parse import urljoin

import requests
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry
from bs4 import BeautifulSoup
from readability import Document

from utils import clean_whitespace

log = logging.getLogger(__name__)

MAX_ARTICLE_PARAGRAPHS = int(os.getenv("NEWS_MAX_ARTICLE_PARAGRAPHS", "6"))
MIN_BODY_CHARS = int(os.getenv("NEWS_MIN_BODY_CHARS", "120"))

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/123.0.0.0 Safari/537.36"
    )
}

# Retries on transient errors; exponential backoff: 1s, 2s, 4s between attempts.
_RETRY = Retry(
    total=3,
    backoff_factor=1,
    status_forcelist=[429, 500, 502, 503, 504],
    allowed_methods=["GET"],
    raise_on_status=False,
)


def _make_session() -> requests.Session:
    session = requests.Session()
    adapter = HTTPAdapter(max_retries=_RETRY)
    session.mount("https://", adapter)
    session.mount("http://", adapter)
    session.headers.update(HEADERS)
    return session


_SESSION = _make_session()


def load_sources(path: str = "config/sources.json") -> list[dict]:
    with open(path, "r", encoding="utf-8") as f:
        return json.load(f)


def fetch_html(url: str, timeout: int = 20) -> str:
    log.debug("GET %s", url)
    response = _SESSION.get(url, timeout=timeout)
    response.raise_for_status()
    return response.text


def extract_homepage_links(source: dict) -> list[dict]:
    log.info("Fetching homepage: %s", source["homepage"])
    html = fetch_html(source["homepage"])
    soup = BeautifulSoup(html, "lxml")

    results = []
    seen = set()

    for el in soup.select(source["link_selector"]):
        title = clean_whitespace(el.get_text(" ", strip=True))
        href = el.get("href")

        if not title or not href or len(title) < 20:
            continue

        url = urljoin(source["base_url"], href)
        key = (title.lower(), url)
        if key in seen:
            continue
        seen.add(key)

        results.append({
            "source": source["name"],
            "title": title,
            "url": url
        })

        if len(results) >= source.get("max_links", 5):
            break

    log.info("Found %d links from %s", len(results), source["name"])
    return results


def extract_article_text(url: str) -> str:
    html = fetch_html(url, timeout=30)
    doc = Document(html)
    article_html = doc.summary()
    soup = BeautifulSoup(article_html, "lxml")
    paragraphs = [clean_whitespace(p.get_text(" ", strip=True)) for p in soup.select("p")]
    paragraphs = [p for p in paragraphs if len(p) > 40]
    return "\n".join(paragraphs[:MAX_ARTICLE_PARAGRAPHS])


def collect_stories() -> list[dict]:
    sources = load_sources()
    stories = []

    for source in sources:
        try:
            links = extract_homepage_links(source)
        except Exception as exc:
            log.warning("Failed fetching source homepage: %s -> %s", source.get("name", "unknown"), exc)
            print(f"[WARN] Failed source homepage: {source.get('name', 'unknown')} -> {exc}")
            continue

        for item in links:
            try:
                body = extract_article_text(item["url"])
                if len(body) < MIN_BODY_CHARS:
                    log.debug("Skipping short article: %s", item["url"])
                    continue

                stories.append({
                    "source": item["source"],
                    "title": item["title"],
                    "url": item["url"],
                    "body": body
                })
                log.debug("Scraped: %s", item["title"])
            except Exception as exc:
                log.warning("Failed parsing article: %s -> %s", item["url"], exc)
                print(f"[WARN] Failed parsing article: {item['url']} -> {exc}")

    log.info("Total stories collected: %d", len(stories))
    return stories
