from __future__ import annotations

import re
import threading
from typing import Optional
from urllib.parse import urlparse

import requests
from playwright.sync_api import TimeoutError as PWTimeoutError

from .cdp_session import get_cdp_browser, invalidate_cdp_browser
from .page_fetch import DEFAULT_UA


_LINKEDIN_SELECTORS = [
    ".jobs-description-content__text",
    ".jobs-box__html-content",
    ".jobs-description__content",
]

_CDP_SEM = threading.Semaphore(1)


def _clean_text(text: str) -> str:
    return re.sub(r"\s+", " ", text or "").strip()


def _pick_linkedin_page(pages, url_substr: str = "linkedin.com"):
    for p in pages:
        try:
            if url_substr in (p.url or ""):
                return p
        except Exception:
            continue
    return pages[0] if pages else None


def _cdp_status_url(cdp_url: str) -> str:
    return cdp_url.rstrip("/") + "/json/version"


def cdp_reachable(cdp_url: str, timeout_s: float = 2.0) -> bool:
    if not cdp_url:
        return False
    try:
        resp = requests.get(_cdp_status_url(cdp_url), timeout=timeout_s)
        return resp.status_code < 400
    except Exception:
        return False


def fetch_linkedin_page_text(
    url: str,
    cdp_url: Optional[str],
    timeout_ms: int = 20_000,
    max_chars: int = 8000,
) -> str:
    if not url or not cdp_url:
        return ""

    parsed = urlparse(url)
    if not parsed.scheme:
        return ""

    with _CDP_SEM:
        for attempt in range(2):
            browser = get_cdp_browser(cdp_url, timeout_ms=max(timeout_ms, 30_000), retries=3, backoff_s=0.8)
            if browser is None:
                return ""

            ctx = browser.contexts[0] if browser.contexts else browser.new_context()

            page = _pick_linkedin_page(ctx.pages)
            created = False
            if page is None:
                page = ctx.new_page()
                created = True

            page.set_default_timeout(timeout_ms)
            page.set_extra_http_headers({"User-Agent": DEFAULT_UA})

            try:
                try:
                    page.goto(url, wait_until="domcontentloaded")
                except PWTimeoutError:
                    pass

                page.wait_for_timeout(1200)

                try:
                    page.wait_for_selector(",".join(_LINKEDIN_SELECTORS), timeout=6_000)
                except PWTimeoutError:
                    pass

                text = page.evaluate(
                    """
                    (selectors) => {
                      const norm = (s) => (s || '').replace(/\s+/g, ' ').trim();
                      for (const sel of selectors) {
                        const el = document.querySelector(sel);
                        if (!el) continue;
                        const t = norm(el.innerText || el.textContent || '');
                        if (t) return t;
                      }
                      return '';
                    }
                    """,
                    _LINKEDIN_SELECTORS,
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
