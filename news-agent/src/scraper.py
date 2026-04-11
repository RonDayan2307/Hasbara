from __future__ import annotations

import json
import logging
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import urljoin, urlparse

import requests
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry
from bs4 import BeautifulSoup
from readability import Document

from settings import RuntimeSettings
from utils import clean_whitespace, project_root, stable_id

log = logging.getLogger(__name__)

_GENERIC_DENY_PATH_FRAGMENTS = (
    "/topic/",
    "/topics/",
    "/tag/",
    "/tags/",
    "/author/",
    "/authors/",
    "/category/",
    "/categories/",
    "/newsletter",
    "/podcast",
    "/podcasts",
    "/video/",
    "/videos/",
    "/audio/",
    "/gallery/",
    "/photos/",
    "/interactive/",
    "/sponsored/",
    "/subscribe",
    "/login",
    "/register",
    "/account/",
    "/commerce/",
)

_GENERIC_DENY_TITLE_FRAGMENTS = (
    "daily edition",
    "monthly update",
    "weekly update",
    "newsletter",
    "podcast",
    "watch live",
    "listen live",
    "audio edition",
)

# Retries on transient errors; exponential backoff: 1s, 2s, 4s between attempts.
_RETRY = Retry(
    total=3,
    backoff_factor=1,
    status_forcelist=[429, 500, 502, 503, 504],
    allowed_methods=["GET"],
    raise_on_status=False,
)


class SkippedArticle(RuntimeError):
    pass


def _make_session() -> requests.Session:
    session = requests.Session()
    adapter = HTTPAdapter(max_retries=_RETRY)
    session.mount("https://", adapter)
    session.mount("http://", adapter)
    return session


def _utc_now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def load_sources(path: str | Path | None = None) -> list[dict]:
    source_path = Path(path) if path else project_root() / "config" / "sources.json"
    if not source_path.is_absolute():
        source_path = project_root() / source_path
    if source_path.suffix.lower() == ".txt":
        return _load_sources_from_text(source_path)
    with source_path.open("r", encoding="utf-8") as f:
        return json.load(f)


def load_source_rules(path: str | Path | None) -> dict[str, dict]:
    if not path:
        return {}

    rules_path = Path(path)
    if not rules_path.is_absolute():
        rules_path = project_root() / rules_path
    if not rules_path.exists():
        return {}

    results: dict[str, dict] = {}
    for raw_line in rules_path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or line.startswith(";"):
            continue

        parts = [clean_whitespace(part) for part in line.split("|")]
        parts = [part for part in parts if part]
        if not parts:
            continue

        name = parts[0]
        rule = {
            "allow_paths": [],
            "deny_paths": [],
            "deny_titles": [],
            "prefer_paths": [],
        }
        for part in parts[1:]:
            if "=" not in part:
                continue
            key, value = part.split("=", 1)
            normalized_key = clean_whitespace(key).lower().replace(" ", "_")
            values = _split_rule_values(value)
            if normalized_key in {"allow", "allow_path", "allow_paths"}:
                rule["allow_paths"] = values
            elif normalized_key in {"deny", "deny_path", "deny_paths"}:
                rule["deny_paths"] = values
            elif normalized_key in {"deny_title", "deny_titles"}:
                rule["deny_titles"] = values
            elif normalized_key in {"prefer", "prefer_path", "prefer_paths"}:
                rule["prefer_paths"] = values
        results[_normalize_source_name(name)] = rule
    return results


def _load_sources_from_text(path: Path) -> list[dict]:
    results: list[dict] = []
    for raw_line in path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or line.startswith(";"):
            continue

        parts = [clean_whitespace(part) for part in line.split("|")]
        parts = [part for part in parts if part]
        if len(parts) < 3:
            continue

        homepage = parts[1]
        parsed = urlparse(homepage)
        base_url = f"{parsed.scheme}://{parsed.netloc}" if parsed.scheme and parsed.netloc else homepage
        try:
            max_links = max(1, int(parts[2]))
        except ValueError:
            max_links = 5

        results.append(
            {
                "name": parts[0],
                "homepage": homepage,
                "base_url": base_url,
                "max_links": max_links,
                "language": parts[3] if len(parts) >= 4 else "unknown",
                "orientation": parts[4] if len(parts) >= 5 else "unknown",
                "priority": int(parts[5]) if len(parts) >= 6 and parts[5].isdigit() else 3,
            }
        )
    return results


def _split_rule_values(value: str) -> list[str]:
    return [item.lower() for item in (clean_whitespace(part) for part in value.split(";")) if item]


def _normalize_source_name(name: str) -> str:
    return clean_whitespace(name).lower()


def _apply_source_rules(source: dict, rules: dict[str, dict]) -> dict:
    enriched = dict(source)
    rule = rules.get(_normalize_source_name(source.get("name", "")), {})
    enriched["allow_paths"] = list(rule.get("allow_paths", []))
    enriched["deny_paths"] = list(rule.get("deny_paths", []))
    enriched["deny_titles"] = list(rule.get("deny_titles", []))
    enriched["prefer_paths"] = list(rule.get("prefer_paths", []))
    return enriched


def fetch_html(session: requests.Session, url: str, timeout: int = 20) -> str:
    log.debug("GET %s", url)
    response = session.get(url, timeout=timeout)
    response.raise_for_status()
    return response.text


def extract_homepage_links(source: dict, session: requests.Session) -> list[dict]:
    log.info("Fetching homepage: %s", source["homepage"])
    html = fetch_html(session, source["homepage"])
    soup = BeautifulSoup(html, "lxml")

    if source.get("link_selector"):
        return _extract_links_by_selector(source, soup)
    return _extract_links_generic(source, soup)


def _base_source_fields(source: dict) -> dict:
    return {
        "source": source["name"],
        "source_language": source.get("language", "unknown"),
        "source_orientation": source.get("orientation", "unknown"),
        "source_priority": int(source.get("priority", 3)),
    }


def _extract_links_by_selector(source: dict, soup: BeautifulSoup) -> list[dict]:
    results = []
    seen = set()
    for el in soup.select(source["link_selector"]):
        title = clean_whitespace(el.get_text(" ", strip=True))
        href = el.get("href")

        if not title or not href or len(title) < 20:
            continue

        url = urljoin(source["base_url"], href)
        skip_reason = _candidate_skip_reason(source, url, title)
        if skip_reason:
            log.debug("Skipping candidate from %s: %s -> %s", source["name"], url, skip_reason)
            continue

        key = (title.lower(), url)
        if key in seen:
            continue
        seen.add(key)

        results.append(
            {
                **_base_source_fields(source),
                "title": title,
                "url": url,
            }
        )

        if len(results) >= source.get("max_links", 5):
            break

    log.info("Found %d links from %s", len(results), source["name"])
    return results


def _extract_links_generic(source: dict, soup: BeautifulSoup) -> list[dict]:
    homepage_netloc = urlparse(source["homepage"]).netloc.lower()
    candidates = []
    seen = set()

    for index, el in enumerate(soup.select("a[href]")):
        href = el.get("href")
        if not href or href.startswith("#") or href.startswith("javascript:") or href.startswith("mailto:"):
            continue

        title = clean_whitespace(
            el.get_text(" ", strip=True)
            or el.get("aria-label")
            or el.get("title")
            or ""
        )
        if len(title) < 25:
            continue

        url = urljoin(source["base_url"], href)
        parsed = urlparse(url)
        if not parsed.scheme.startswith("http") or not parsed.netloc:
            continue

        if homepage_netloc and parsed.netloc.lower() != homepage_netloc:
            continue

        skip_reason = _candidate_skip_reason(source, url, title)
        if skip_reason:
            continue

        key = (title.lower(), url)
        if key in seen:
            continue
        seen.add(key)

        score = _score_link_candidate(source, url, title)
        if score <= 0:
            continue

        candidates.append(
            (
                -score,
                index,
                {
                    **_base_source_fields(source),
                    "title": title,
                    "url": url,
                },
            )
        )

    candidates.sort()
    results = [entry for _, _, entry in candidates[: source.get("max_links", 5)]]
    log.info("Found %d links from %s", len(results), source["name"])
    return results


def _score_link_candidate(source: dict, url: str, title: str) -> int:
    parsed = urlparse(url)
    path = parsed.path.lower()
    lowered_title = title.lower()
    score = 0

    if 30 <= len(title) <= 170:
        score += 3
    elif len(title) >= 20:
        score += 1

    if path.count("/") >= 2:
        score += 1

    for token in ("article", "story", "news", "world", "politic", "middle-east", "israel", "2026", "2025", "2024"):
        if token in path:
            score += 1

    for token in ("video", "live", "gallery", "photo", "podcast", "newsletter", "opinion"):
        if token in path:
            score -= 2

    for token in ("exclusive", "analysis", "ceasefire", "iran", "gaza", "hezbollah", "hamas", "netanyahu"):
        if token in lowered_title:
            score += 1

    for token in source.get("prefer_paths", []):
        if token in path:
            score += 2

    if parsed.query:
        score -= 1

    return score


def _candidate_skip_reason(source: dict, url: str, title: str) -> str | None:
    path = urlparse(url).path.lower()
    lowered_title = title.lower()

    for token in _GENERIC_DENY_PATH_FRAGMENTS:
        if token in path:
            return f"blocked by generic path rule: {token}"

    for token in _GENERIC_DENY_TITLE_FRAGMENTS:
        if token in lowered_title:
            return f"blocked by generic title rule: {token}"

    for token in source.get("deny_paths", []):
        if token and token in path:
            return f"blocked by source path rule: {token}"

    for token in source.get("deny_titles", []):
        if token and token in lowered_title:
            return f"blocked by source title rule: {token}"

    allow_paths = source.get("allow_paths", [])
    if allow_paths and not any(token in path for token in allow_paths):
        return "path did not match any allow rule"

    return None


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


def extract_article(
    url: str,
    session: requests.Session,
    settings: RuntimeSettings,
    *,
    source: dict | None = None,
) -> dict:
    html = fetch_html(session, url, timeout=30)
    full_soup = BeautifulSoup(html, "lxml")
    page_title = clean_whitespace(full_soup.title.get_text(" ", strip=True) if full_soup.title else "")
    skip_reason = _candidate_skip_reason(source or {}, url, page_title)
    if skip_reason:
        raise SkippedArticle(skip_reason)

    og_type = (_extract_meta(full_soup, "og:type") or "").lower()
    if og_type in {"website", "profile"}:
        raise SkippedArticle(f"unsupported og:type {og_type}")

    doc = Document(html)
    article_html = doc.summary()
    soup = BeautifulSoup(article_html, "lxml")
    paragraphs = [clean_whitespace(p.get_text(" ", strip=True)) for p in soup.select("p")]
    paragraphs = [p for p in paragraphs if len(p) > 40]
    if not paragraphs:
        raise SkippedArticle("no article-like paragraphs extracted")

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
        "body": "\n".join(paragraphs[:settings.max_article_paragraphs]),
        "canonical_url": canonical,
        "published_at": published_at,
        "description": _extract_meta(full_soup, "og:description", "description"),
        "metrics": _extract_metrics(full_soup),
    }


def extract_article_text(url: str) -> str:
    raise RuntimeError("Use extract_article(url, session, settings) instead.")


def iter_stories(settings: RuntimeSettings, limit: int | None = None):
    source_rules = load_source_rules(settings.source_rules_path)
    sources = [
        _apply_source_rules(source, source_rules)
        for source in sorted(
            load_sources(settings.source_config_path),
            key=lambda source: int(source.get("priority", 3)),
            reverse=True,
        )
    ]
    collected_at = _utc_now()
    session = _make_session()
    session.headers.update({"User-Agent": settings.user_agent})
    yielded = 0
    source_states = [
        {
            "source": source,
            "links": None,
            "next_index": 0,
            "exhausted": False,
        }
        for source in sources
    ]

    while True:
        made_progress = False
        active_sources = 0

        for state in source_states:
            if limit is not None and yielded >= limit:
                return
            if state["exhausted"]:
                continue

            active_sources += 1
            source = state["source"]

            if state["links"] is None:
                try:
                    state["links"] = extract_homepage_links(source, session)
                except Exception as exc:
                    log.warning("Failed fetching source homepage: %s -> %s", source.get("name", "unknown"), exc)
                    print(f"[WARN] Failed source homepage: {source.get('name', 'unknown')} -> {exc}")
                    state["exhausted"] = True
                    continue

            links = state["links"] or []
            if state["next_index"] >= len(links):
                state["exhausted"] = True
                continue

            item = links[state["next_index"]]
            state["next_index"] += 1
            made_progress = True

            try:
                article = extract_article(item["url"], session, settings, source=source)
                body = article["body"]
                if len(body) < settings.min_body_chars:
                    log.info("Skipping short or thin article: %s", item["url"])
                    continue

                url = article.get("canonical_url") or item["url"]
                yielded += 1
                yield {
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
                log.debug("Scraped: %s", item["title"])
            except SkippedArticle as exc:
                log.info("Skipping non-article candidate: %s -> %s", item["url"], exc)
            except Exception as exc:
                log.warning("Failed parsing article: %s -> %s", item["url"], exc)
                print(f"[WARN] Failed parsing article: {item['url']} -> {exc}")

        if active_sources == 0 or not made_progress:
            return


def collect_stories(settings: RuntimeSettings) -> list[dict]:
    stories = list(iter_stories(settings))
    log.info("Total stories collected: %d", len(stories))
    return stories
