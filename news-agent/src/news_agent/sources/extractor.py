"""Article content extraction."""

from __future__ import annotations

import logging
import re

import requests
from bs4 import BeautifulSoup

from ..utils.text import clean_text, word_count

logger = logging.getLogger("news_agent.sources.extractor")


def extract_article(url: str, user_agent: str, timeout: int = 30) -> dict:
    """Extract article metadata and body text from a URL."""
    result = {
        "title": "",
        "author": "",
        "body_text": "",
        "word_count": 0,
        "published": None,
    }

    try:
        resp = requests.get(url, timeout=timeout,
                            headers={"User-Agent": user_agent})
        resp.raise_for_status()
    except Exception as e:
        logger.warning(f"Article fetch failed for {url}: {e}")
        return result

    soup = BeautifulSoup(resp.text, "lxml")

    # Title
    og_title = soup.find("meta", property="og:title")
    if og_title and og_title.get("content"):
        result["title"] = og_title["content"].strip()
    elif soup.title:
        result["title"] = soup.title.get_text(strip=True)

    # Author
    author_meta = soup.find("meta", attrs={"name": "author"})
    if author_meta and author_meta.get("content"):
        result["author"] = author_meta["content"].strip()

    # Published date
    for attr in ["article:published_time", "datePublished", "date"]:
        dt_meta = soup.find("meta", property=attr) or soup.find("meta", attrs={"name": attr})
        if dt_meta and dt_meta.get("content"):
            result["published"] = dt_meta["content"].strip()
            break

    # Also check JSON-LD
    for script in soup.find_all("script", type="application/ld+json"):
        try:
            import json
            ld = json.loads(script.string or "")
            if isinstance(ld, dict):
                if "datePublished" in ld and not result["published"]:
                    result["published"] = ld["datePublished"]
                if "author" in ld and not result["author"]:
                    author = ld["author"]
                    if isinstance(author, dict):
                        result["author"] = author.get("name", "")
                    elif isinstance(author, list) and author:
                        result["author"] = author[0].get("name", "") if isinstance(author[0], dict) else str(author[0])
        except Exception:
            pass

    # Body text extraction
    # Remove non-content elements
    for tag in soup.find_all(["script", "style", "nav", "header", "footer",
                               "aside", "iframe", "form", "button"]):
        tag.decompose()

    # Try article tag first
    article = soup.find("article")
    if article:
        paragraphs = article.find_all("p")
    else:
        # Fall back to main or body
        main = soup.find("main") or soup.find("div", role="main") or soup.body
        paragraphs = main.find_all("p") if main else []

    text_parts = []
    for p in paragraphs:
        t = p.get_text(strip=True)
        if len(t) > 30:  # Skip very short fragments
            text_parts.append(t)

    body = clean_text(" ".join(text_parts))
    result["body_text"] = body
    result["word_count"] = word_count(body)

    return result
