from __future__ import annotations

import json
import logging
import os
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import urljoin

import requests
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry
from bs4 import BeautifulSoup
from readability import Document

from utils import clean_whitespace, project_root, stable_id

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


def _utc_now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def load_sources(path: str | Path | None = None) -> list[dict]:
    source_path = Path(path) if path else project_root() / "config" / "sources.json"
    if not source_path.is_absolute():
        source_path = project_root() / source_path
    with source_path.open("r", encoding="utf-8") as f:
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

        results.append(
            {
                "source": source["name"],
                "source_language": source.get("language", "unknown"),
                "source_orientation": source.get("orientation", "unknown"),
                "source_priority": int(source.get("priority", 3)),
                "title": title,
                "url": url,
            }
        )

        if len(results) >= source.get("max_links", 5):
            break

    log.info("Found %d links from %s", len(results), source["name"])
    return results


def _extract_meta(soup: BeautifulSoup, *names: str) -> str | None:
    for name in names:
        tag = soup.find("meta", attrs={"property": name}) or soup.find("meta", attrs={"name": name})
        if tag and tag.get("content"):
            return clean_whitespace(tag["content"])
    return None


def _extract_metrics(_soup: BeautifulSoup) -> dict:
    return {
        "views": None,
        "likes": None,
        "shares": None,
        "comments": None,
    }


def extract_article(url: str) -> dict:
    html = fetch_html(url, timeout=30)
    doc = Document(html)
    article_html = doc.summary()
    soup = BeautifulSoup(article_html, "lxml")
    full_soup = BeautifulSoup(html, "lxml")
    paragraphs = [clean_whitespace(p.get_text(" ", strip=True)) for p in soup.select("p")]
    paragraphs = [p for p in paragraphs if len(p) > 40]
    canonical = _extract_meta(full_soup, "og:url") or url
    published_at = _extract_meta(
        full_soup,
        "article:published_time",
        "datePublished",
        "pubdate",
        "date",
        "DC.date.issued",
    )
    return {
        "body": "\n".join(paragraphs[:MAX_ARTICLE_PARAGRAPHS]),
        "canonical_url": canonical,
        "published_at": published_at,
        "description": _extract_meta(full_soup, "og:description", "description"),
        "metrics": _extract_metrics(full_soup),
    }


def extract_article_text(url: str) -> str:
    return extract_article(url)["body"]


def collect_stories() -> list[dict]:
    sources = load_sources()
    stories = []
    collected_at = _utc_now()

    for source in sources:
        try:
            links = extract_homepage_links(source)
        except Exception as exc:
            log.warning("Failed fetching source homepage: %s -> %s", source.get("name", "unknown"), exc)
            print(f"[WARN] Failed source homepage: {source.get('name', 'unknown')} -> {exc}")
            continue

        for item in links:
            try:
                article = extract_article(item["url"])
                body = article["body"]
                if len(body) < MIN_BODY_CHARS:
                    log.debug("Skipping short article: %s", item["url"])
                    continue

                url = article.get("canonical_url") or item["url"]
                stories.append(
                    {
                        "id": stable_id(url, item["title"]),
                        "source": item["source"],
                        "source_language": item.get("source_language", "unknown"),
                        "source_orientation": item.get("source_orientation", "unknown"),
                        "source_priority": item.get("source_priority", 3),
                        "title": item["title"],
                        "url": url,
                        "body": body,
                        "description": article.get("description"),
                        "published_at": article.get("published_at"),
                        "collected_at": collected_at,
                        "metrics": article.get("metrics", {}),
                    }
                )
                log.debug("Scraped: %s", item["title"])
            except Exception as exc:
                log.warning("Failed parsing article: %s -> %s", item["url"], exc)
                print(f"[WARN] Failed parsing article: {item['url']} -> {exc}")

    log.info("Total stories collected: %d", len(stories))
    return stories
