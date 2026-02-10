from __future__ import annotations

import re
from typing import Optional
from urllib.parse import urlparse

import requests

try:
    from selectolax.parser import HTMLParser
except Exception:  # pragma: no cover - optional dependency
    HTMLParser = None


DEFAULT_UA = "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"


def _clean_text(text: str) -> str:
    text = re.sub(r"\s+", " ", text or "").strip()
    return text


def fetch_page_text(url: str, timeout_s: int = 20, max_chars: int = 8000) -> str:
    """Fetch a URL and return readable text.

    Uses selectolax if available; otherwise falls back to a simple tag-stripping approach.
    """

    if not url:
        return ""

    parsed = urlparse(url)
    if not parsed.scheme:
        return ""

    headers = {"User-Agent": DEFAULT_UA}
    resp = requests.get(url, headers=headers, timeout=timeout_s)
    if resp.status_code >= 400:
        return ""

    content_type = (resp.headers.get("content-type") or "").lower()
    if "text" not in content_type and "html" not in content_type:
        # best-effort: still try to read text
        pass

    html = resp.text or ""

    text: Optional[str] = None
    if HTMLParser is not None:
        tree = HTMLParser(html)
        # remove noisy nodes
        for node in tree.css("script, style, noscript"):
            node.decompose()
        if tree.body is not None:
            text = tree.body.text(separator=" ")
        else:
            text = tree.text(separator=" ")
    else:
        # Very simple fallback: drop tags.
        text = re.sub(r"<[^>]+>", " ", html)

    text = _clean_text(text or "")
    if max_chars and len(text) > max_chars:
        text = text[:max_chars]
    return text
