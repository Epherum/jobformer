from __future__ import annotations

import re
import threading
from typing import Optional
from urllib.parse import urlparse

from playwright.sync_api import TimeoutError as PWTimeoutError
from playwright.sync_api import sync_playwright

from .cdp_session import get_cdp_browser, invalidate_cdp_browser
from .page_fetch import DEFAULT_UA


_DEFAULT_SELECTORS = [
    "main",
    "article",
    "[role='main']",
    ".content",
    "body",
]

# Serialize CDP navigation/extraction. CDP Chrome is a shared singleton.
_CDP_SEM = threading.Semaphore(1)


def _clean_text(text: str) -> str:
    return re.sub(r"\s+", " ", text or "").strip()


def fetch_page_text_via_cdp(
    url: str,
    cdp_url: Optional[str],
    *,
    timeout_ms: int = 45_000,
    max_chars: int = 8000,
    selectors: Optional[list[str]] = None,
) -> str:
    """Fetch readable text for any URL via an existing CDP Chrome session.

    Use this as a fallback for sites blocked to raw HTTP (Cloudflare, bot checks).
    """

    if not url or not cdp_url:
        return ""

    parsed = urlparse(url)
    if not parsed.scheme:
        return ""

    sels = selectors or _DEFAULT_SELECTORS

    with _CDP_SEM:
        try:
            browser = get_cdp_browser(cdp_url, timeout_ms=timeout_ms, raise_on_fail=False)
            if browser is None:
                return ""
            ctx = browser.contexts[0] if browser.contexts else browser.new_context()
            page = ctx.new_page()
            page.set_default_timeout(timeout_ms)
            try:
                try:
                    page.set_extra_http_headers({"User-Agent": DEFAULT_UA})
                except Exception:
                    pass

                try:
                    page.goto(url, wait_until="domcontentloaded")
                except PWTimeoutError:
                    # Best-effort: continue to extraction.
                    pass

                # Cloudflare / bot checks can show an interstitial for a few seconds.
                # Wait until it disappears (or we time out) before extracting.
                for _ in range(20):
                    try:
                        body_txt = (page.inner_text("body") or "")
                    except Exception:
                        body_txt = ""
                    if body_txt and ("Verifying you are human" not in body_txt) and ("Just a moment" not in body_txt):
                        break
                    page.wait_for_timeout(1000)

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
                      return norm(document.body?.innerText || '');
                    }
                    """,
                    sels,
                )

                text = _clean_text(text or "")
                if max_chars and len(text) > max_chars:
                    text = text[:max_chars]
                return text
            finally:
                try:
                    page.close()
                except Exception:
                    pass
        except Exception:
            # Invalidate so next call reconnects.
            try:
                invalidate_cdp_browser()
            except Exception:
                pass
            return ""
