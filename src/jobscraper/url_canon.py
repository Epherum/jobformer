from __future__ import annotations

from urllib.parse import parse_qsl, urlencode, urlparse, urlunparse


# Common tracking params we want to ignore for cache key stability.
DROP_PARAMS = {
    # LinkedIn
    "trk",
    "trackingId",
    "refId",
    "eBP",
    "alternateChannel",
    "lipi",
    "originalSubdomain",
    # Generic marketing
    "utm_source",
    "utm_medium",
    "utm_campaign",
    "utm_term",
    "utm_content",
    "gclid",
    "fbclid",
}


def canonicalize_url(url: str) -> str:
    """Return a stable URL used as cache key.

    - Lowercase scheme/host
    - Remove fragments
    - Drop common tracking query params
    - Keep remaining query params in sorted order
    - Normalize trailing slash (strip unless root)
    """

    if not url:
        return ""

    p = urlparse(url.strip())
    scheme = (p.scheme or "").lower()
    netloc = (p.netloc or "").lower()
    path = p.path or ""

    if path != "/":
        path = path.rstrip("/")

    q = []
    for k, v in parse_qsl(p.query, keep_blank_values=True):
        if k in DROP_PARAMS:
            continue
        q.append((k, v))
    q.sort()
    query = urlencode(q, doseq=True)

    return urlunparse((scheme, netloc, path, "", query, ""))
