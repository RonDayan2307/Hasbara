"""URL canonicalization for deduplication."""

from __future__ import annotations

from urllib.parse import urlparse, urlunparse, parse_qs, urlencode


# Query parameters to strip for canonicalization
STRIP_PARAMS = {
    "utm_source", "utm_medium", "utm_campaign", "utm_term", "utm_content",
    "fbclid", "gclid", "ref", "source", "ncid", "sr", "_ga",
}


def canonicalize_url(url: str) -> str:
    """Normalize a URL for deduplication."""
    parsed = urlparse(url)

    # Lowercase scheme and host
    scheme = parsed.scheme.lower()
    netloc = parsed.netloc.lower()

    # Remove www. prefix
    if netloc.startswith("www."):
        netloc = netloc[4:]

    # Remove trailing slash from path
    path = parsed.path.rstrip("/") or "/"

    # Strip tracking query params
    qs = parse_qs(parsed.query, keep_blank_values=False)
    filtered = {k: v for k, v in qs.items() if k.lower() not in STRIP_PARAMS}
    query = urlencode(filtered, doseq=True) if filtered else ""

    # Drop fragment
    return urlunparse((scheme, netloc, path, "", query, ""))
