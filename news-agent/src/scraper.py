from __future__ import annotations

import json
import logging
import re
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import urljoin, urlparse

import requests
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry
from bs4 import BeautifulSoup
from readability import Document

from contracts import Story
from settings import RuntimeSettings
from telemetry import IngestionTelemetry
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
    "explainer",
    "what to know",
    "live blog",
)

_SOURCE_SUBTITLE_SELECTORS = {
    "times of israel": [".article-subtitle", "h2.headline-secondary", ".the-content h2:first-of-type"],
    "jerusalem post": [".article-sub-title", ".article-header h2"],
    "haaretz english": ["[data-testid='article-sub-title']", ".article-header-subtitle", "header h2"],
    "ynet news": [".art_header_sub_title", ".sub-title"],
    "ap world": [".Page-deck", ".RichTextStoryBody-deck"],
    "bbc middle east": ["[data-component='headline-block'] p", "article header p"],
    "guardian middle east": [".content__standfirst p", "[data-gu-name='standfirst'] p"],
}

_GENERIC_SUBTITLE_SELECTORS = [
    ".article-subtitle",
    ".deck",
    ".standfirst",
    "h2.subtitle",
    "[data-testid='article-subtitle']",
    ".article-summary",
    "header h2",
]

_SOURCE_BODY_SELECTORS = {
    "times of israel": ["article p", ".the-content p", ".entry-content p"],
    "jerusalem post": ["article p", ".article-body p", ".itemFullText p"],
    "haaretz english": ["article p", "[data-testid='article-body'] p"],
    "ynet news": ["article p", ".art_body p", ".article-body__content p"],
    "ap world": ["article p", "[data-key='article'] p", ".RichTextStoryBody p"],
    "bbc middle east": ["[data-component='text-block'] p", "main article p", "article p"],
    "guardian middle east": ["article p", "[id='maincontent'] p", ".article-body-commercial-selector p"],
}

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

        source = {
            "name": parts[0],
            "homepage": homepage,
            "base_url": base_url,
            "max_links": max_links,
            "language": parts[3] if len(parts) >= 4 else "unknown",
            "orientation": parts[4] if len(parts) >= 5 else "unknown",
            "priority": int(parts[5]) if len(parts) >= 6 and parts[5].isdigit() else 3,
            "fallback_homepages": [],
            "warn_on_failure": True,
        }

        for part in parts[6:]:
            if "=" not in part:
                continue
            key, value = part.split("=", 1)
            normalized_key = clean_whitespace(key).lower().replace(" ", "_")
            normalized_value = clean_whitespace(value)
            if not normalized_value:
                continue
            if normalized_key in {"fallback_homepages", "homepage_fallbacks"}:
                source["fallback_homepages"] = [
                    clean_whitespace(item)
                    for item in normalized_value.split(";")
                    if clean_whitespace(item)
                ]
            elif normalized_key in {"warn_on_failure", "emit_failure_warning"}:
                source["warn_on_failure"] = normalized_value.lower() not in {"0", "false", "no", "off"}
            else:
                source[normalized_key] = normalized_value

        results.append(source)
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


def extract_homepage_links(source: dict, session: requests.Session, homepage_url: str | None = None) -> list[dict]:
    homepage = homepage_url or source["homepage"]
    log.info("Fetching homepage: %s", homepage)
    html = fetch_html(session, homepage)
    soup = BeautifulSoup(html, "lxml")

    effective_source = dict(source)
    effective_source["homepage"] = homepage
    parsed = urlparse(homepage)
    if parsed.scheme and parsed.netloc:
        effective_source["base_url"] = f"{parsed.scheme}://{parsed.netloc}"

    if effective_source.get("link_selector"):
        return _extract_links_by_selector(effective_source, soup)
    return _extract_links_generic(effective_source, soup)


def _fetch_source_links(source: dict, session: requests.Session) -> tuple[list[dict], str]:
    candidates = [source["homepage"], *source.get("fallback_homepages", [])]
    errors: list[str] = []
    for homepage in candidates:
        try:
            links = extract_homepage_links(source, session, homepage_url=homepage)
            return links, homepage
        except Exception as exc:
            errors.append(f"{homepage} -> {exc}")
    raise RuntimeError(" | ".join(errors) if errors else "no homepage candidates were available")


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


def _extract_subtitle(soup: BeautifulSoup, source: dict | None) -> str | None:
    """Extract subtitle/secondary title from the article page."""
    # Try source-specific selectors first
    if source:
        name = _normalize_source_name(source.get("name", ""))
        source_selectors = _SOURCE_SUBTITLE_SELECTORS.get(name, [])
        for selector in source_selectors:
            elements = soup.select(selector)
            for element in elements:
                text = clean_whitespace(element.get_text(" ", strip=True))
                if text and len(text) > 10:
                    return text

    # Try generic subtitle selectors
    for selector in _GENERIC_SUBTITLE_SELECTORS:
        elements = soup.select(selector)
        for element in elements:
            text = clean_whitespace(element.get_text(" ", strip=True))
            if text and len(text) > 10:
                return text

    # Compare og:title vs page title — subtitle may be the difference
    og_title = _extract_meta(soup, "og:title")
    page_title = clean_whitespace(soup.title.get_text(" ", strip=True) if soup.title else "")
    if og_title and page_title and og_title != page_title:
        # If one is substantially longer, it may contain the subtitle
        if len(og_title) > len(page_title) + 15:
            extra = og_title.replace(page_title, "").strip(" -|:")
            if extra and len(extra) > 10:
                return extra

    return None


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

    paragraphs = _extract_source_paragraphs(full_soup, source)
    if not paragraphs:
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
        "subtitle": _extract_subtitle(full_soup, source),
        "canonical_url": canonical,
        "published_at": published_at,
        "description": _extract_meta(full_soup, "og:description", "description"),
        "metrics": _extract_metrics(full_soup),
    }


def extract_article_text(url: str) -> str:
    raise RuntimeError("Use extract_article(url, session, settings) instead.")


def iter_stories(
    settings: RuntimeSettings,
    limit: int | None = None,
    telemetry: IngestionTelemetry | None = None,
):
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
    session.headers.update(
        {
            "User-Agent": settings.user_agent,
            "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
            "Accept-Language": "en-US,en;q=0.9",
            "Cache-Control": "no-cache",
            "Pragma": "no-cache",
            "Upgrade-Insecure-Requests": "1",
        }
    )
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
                    state["links"], actual_homepage = _fetch_source_links(source, session)
                    if actual_homepage != source["homepage"]:
                        log.info(
                            "Using fallback homepage for %s: %s",
                            source.get("name", "unknown"),
                            actual_homepage,
                        )
                    if telemetry is not None:
                        telemetry.record_homepage_success(source, links_found=len(state["links"] or []))
                except Exception as exc:
                    emit_warning = bool(source.get("warn_on_failure", True))
                    log_fn = log.warning if emit_warning else log.info
                    log_fn("Failed fetching source homepage: %s -> %s", source.get("name", "unknown"), exc)
                    if emit_warning:
                        print(f"[WARN] Failed source homepage: {source.get('name', 'unknown')} -> {exc}")
                    if telemetry is not None:
                        telemetry.record_homepage_failure(source, str(exc))
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
                    if telemetry is not None:
                        telemetry.record_candidate_skip(source, "short or thin article body")
                    continue

                url = article.get("canonical_url") or item["url"]
                yielded += 1
                if telemetry is not None:
                    telemetry.record_story_collected(source)
                yield {
                    "id": stable_id(url, item["title"]),
                    "source": item["source"],
                    "source_language": item.get("source_language", "unknown"),
                    "source_orientation": item.get("source_orientation", "unknown"),
                    "source_priority": item.get("source_priority", 3),
                    "title": item["title"],
                    "subtitle": article.get("subtitle"),
                    "url": url,
                    "body": body,
                    "description": article.get("description"),
                    "published_at": article.get("published_at"),
                    "collected_at": collected_at,
                    "metrics": article.get("metrics", {}),
                }  # type: Story
                log.debug("Scraped: %s", item["title"])
            except SkippedArticle as exc:
                log.info("Skipping non-article candidate: %s -> %s", item["url"], exc)
                if telemetry is not None:
                    telemetry.record_candidate_skip(source, str(exc))
            except Exception as exc:
                log.warning("Failed parsing article: %s -> %s", item["url"], exc)
                print(f"[WARN] Failed parsing article: {item['url']} -> {exc}")
                if telemetry is not None:
                    telemetry.record_extraction_failure(source, str(exc))

        if active_sources == 0 or not made_progress:
            return


def collect_stories(
    settings: RuntimeSettings,
    *,
    telemetry: IngestionTelemetry | None = None,
) -> list[Story]:
    stories = list(iter_stories(settings, telemetry=telemetry))
    log.info("Total stories collected: %d", len(stories))
    return stories


def _extract_source_paragraphs(soup: BeautifulSoup, source: dict | None) -> list[str]:
    selectors = _selectors_for_source(source)
    for selector in selectors:
        paragraphs = [
            clean_whitespace(p.get_text(" ", strip=True))
            for p in soup.select(selector)
            if clean_whitespace(p.get_text(" ", strip=True))
        ]
        paragraphs = [paragraph for paragraph in paragraphs if len(paragraph) > 40]
        if paragraphs:
            return _dedupe_paragraphs(paragraphs)

    return _extract_json_ld_article_body(soup)


def _selectors_for_source(source: dict | None) -> list[str]:
    if not source:
        return []
    name = _normalize_source_name(source.get("name", ""))
    selectors = list(_SOURCE_BODY_SELECTORS.get(name, []))
    selectors.extend(["[itemprop='articleBody'] p", "main article p", "article p"])
    seen = []
    for selector in selectors:
        if selector not in seen:
            seen.append(selector)
    return seen


def _extract_json_ld_article_body(soup: BeautifulSoup) -> list[str]:
    for script in soup.select("script[type='application/ld+json']"):
        raw_json = clean_whitespace(script.get_text(" ", strip=True))
        if not raw_json:
            continue
        try:
            payload = json.loads(raw_json)
        except json.JSONDecodeError:
            continue
        article_body = _find_article_body(payload)
        if article_body:
            parts = [
                clean_whitespace(part)
                for part in re.split(r"(?:\n{2,}|(?<=[.!?])\s{2,})", article_body)
                if clean_whitespace(part)
            ]
            parts = [part for part in parts if len(part) > 40]
            if parts:
                return _dedupe_paragraphs(parts)
    return []


def _find_article_body(payload: object) -> str | None:
    if isinstance(payload, dict):
        article_body = payload.get("articleBody")
        if isinstance(article_body, str) and clean_whitespace(article_body):
            return clean_whitespace(article_body)
        graph = payload.get("@graph")
        if graph is not None:
            found = _find_article_body(graph)
            if found:
                return found
        for value in payload.values():
            found = _find_article_body(value)
            if found:
                return found
    elif isinstance(payload, list):
        for item in payload:
            found = _find_article_body(item)
            if found:
                return found
    return None


def _dedupe_paragraphs(paragraphs: list[str]) -> list[str]:
    seen: list[str] = []
    for paragraph in paragraphs:
        if paragraph not in seen:
            seen.append(paragraph)
    return seen
