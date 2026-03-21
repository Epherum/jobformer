from __future__ import annotations

from urllib.parse import parse_qsl, urlencode, urlparse, urlunparse
import re


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

    # Site-specific normalization.
    # Tanitjobs sometimes uses a short URL (/job/<id>/) and later redirects to a long slug URL
    # (/job/<id>/<slug>/). We canonicalize both to /job/<id> so sheet URLs match open tabs.
    if "tanitjobs.com" in netloc:
        m = re.match(r"^/job/(?P<id>\d+)(?:/.*)?$", path)
        if m:
            path = f"/job/{m.group('id')}"

    # LinkedIn job pages appear in two shapes:
    # - /jobs/view/<id>/
    # - /jobs/view/<slug>-<id>
    # For cache stability we canonicalize both to /jobs/view/<id> and drop all query params.
    keep_query = True
    if "linkedin.com" in netloc:
        m = re.match(r"^/jobs/view/(?:.+-)?(?P<id>\d+)(?:/.*)?$", path)
        if m:
            path = f"/jobs/view/{m.group('id')}"
            keep_query = False

    if path != "/":
        path = path.rstrip("/")

    q = []
    if keep_query:
        for k, v in parse_qsl(p.query, keep_blank_values=True):
            if k in DROP_PARAMS:
                continue
            q.append((k, v))
    q.sort()
    query = urlencode(q, doseq=True)

    return urlunparse((scheme, netloc, path, "", query, ""))
