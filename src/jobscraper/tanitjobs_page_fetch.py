from __future__ import annotations

import re
import threading
from typing import Optional
from urllib.parse import urlparse

from playwright.sync_api import TimeoutError as PWTimeoutError

from .cdp_session import get_cdp_browser, invalidate_cdp_browser
from .page_fetch import DEFAULT_UA


_TANIT_SELECTORS = [
    "main",
    "article",
    ".job-description",
    "[class*='description']",
    "[class*='content']",
]

# CDP is a single shared browser. Serialize page navigation/extraction.
_CDP_SEM = threading.Semaphore(1)


def _clean_text(text: str) -> str:
    return re.sub(r"\s+", " ", text or "").strip()


def _pick_tanit_page(pages, url_substr: str = "tanitjobs.com"):
    for p in pages:
        try:
            if url_substr in (p.url or ""):
                return p
        except Exception:
            continue
    return pages[0] if pages else None


def fetch_tanitjobs_page_text(
    url: str,
    cdp_url: Optional[str],
    timeout_ms: int = 45_000,
    max_chars: int = 8000,
) -> str:
    """Fetch Tanitjobs job detail text via an existing CDP Chrome session.

    Tanitjobs often uses Cloudflare. Using the authenticated/cleared browser session
    is usually more reliable than raw HTTP.
    """

    if not url or not cdp_url:
        return ""

    parsed = urlparse(url)
    if not parsed.scheme:
        return ""

    with _CDP_SEM:
        for attempt in range(2):
            browser = get_cdp_browser(cdp_url, timeout_ms=max(timeout_ms, 45_000), retries=3, backoff_s=0.8)
            if browser is None:
                return ""

            ctx = browser.contexts[0] if browser.contexts else browser.new_context()

            page = _pick_tanit_page(ctx.pages)
            created = False
            if page is None:
                page = ctx.new_page()
                created = True

            page.set_default_timeout(timeout_ms)
            try:
                page.set_extra_http_headers({"User-Agent": DEFAULT_UA})
            except Exception:
                pass

            try:
                try:
                    page.goto(url, wait_until="domcontentloaded")
                except PWTimeoutError:
                    # Best-effort: page may still load enough to extract.
                    pass

                page.wait_for_timeout(1200)

                text = page.evaluate(
                    """
                    (selectors) => {
                      const norm = (s) => (s || '').replace(/\s+/g, ' ').trim();
                      for (const sel of selectors) {
                        const el = document.querySelector(sel);
                        if (!el) continue;
                        const t = norm(el.innerText || el.textContent || '');
                        if (t && t.length > 200) return t;
                      }
                      // fallback: entire body
                      return norm(document.body?.innerText || '');
                    }
                    """,
                    _TANIT_SELECTORS,
                )

                text = _clean_text(text or "")
                if max_chars and len(text) > max_chars:
                    text = text[:max_chars]
                return text
            except Exception:
                invalidate_cdp_browser()
                if attempt == 0:
                    continue
                return ""
            finally:
                try:
                    if created:
                        page.close()
                except Exception:
                    pass
