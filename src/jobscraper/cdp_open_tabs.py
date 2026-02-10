from __future__ import annotations

import re
import threading
from dataclasses import dataclass
from typing import Optional
from urllib.parse import urlparse

from playwright.sync_api import TimeoutError as PWTimeoutError

from .cdp_session import get_cdp_browser, invalidate_cdp_browser
from .page_fetch import DEFAULT_UA
from .tanitjobs_page_fetch import _TANIT_SELECTORS  # reuse

_DEFAULT_SELECTORS = [
    "main",
    "article",
    "[role='main']",
    ".content",
    "body",
]

# Serialize CDP interaction (shared singleton browser)
_CDP_SEM = threading.Semaphore(1)


@dataclass(frozen=True)
class OpenTabText:
    url: str
    text: str
    status: str  # ok|blocked|empty|error
    error: str | None = None


def _clean_text(text: str) -> str:
    return re.sub(r"\s+", " ", text or "").strip()


def _is_blocked_text(text: str) -> bool:
    t = (text or "").lower()
    if not t:
        return False
    blocked_markers = [
        "just a moment",
        "verifying you are human",
        "cloudflare",
        "access denied",
        "blocked",
        "captcha",
    ]
    return any(m in t for m in blocked_markers)


def _classify_text(text: str) -> str:
    if _is_blocked_text(text):
        return "blocked"
    if not text or len(text) < 200:
        return "empty"
    return "ok"


def _valid_http_url(url: str) -> bool:
    if not url:
        return False
    try:
        p = urlparse(url)
    except Exception:
        return False
    return p.scheme in {"http", "https"} and bool(p.netloc)


def _selectors_for_url(url: str) -> list[str]:
    host = (urlparse(url).netloc or "").lower()
    if "tanitjobs.com" in host:
        return list(_TANIT_SELECTORS)
    return list(_DEFAULT_SELECTORS)


def extract_text_from_open_tabs(
    *,
    cdp_url: Optional[str],
    max_tabs: int = 25,
    timeout_ms: int = 30_000,
) -> list[OpenTabText]:
    """Extract readable text from the currently open tabs in the CDP session.

    This does NOT navigate. It reads whatever is currently loaded in each open page.
    """

    if not cdp_url:
        return []

    with _CDP_SEM:
        try:
            browser = get_cdp_browser(cdp_url, timeout_ms=max(timeout_ms, 45_000), retries=3, backoff_s=0.8)
            if browser is None:
                return []

            ctx = browser.contexts[0] if browser.contexts else browser.new_context()
            pages = list(ctx.pages)
            out: list[OpenTabText] = []

            # Most recently opened tabs are typically at the end. Prefer those.
            for page in reversed(pages):
                if len(out) >= max_tabs:
                    break
                try:
                    url = page.url or ""
                except Exception:
                    url = ""

                if not _valid_http_url(url):
                    continue

                try:
                    page.set_default_timeout(timeout_ms)
                    try:
                        page.set_extra_http_headers({"User-Agent": DEFAULT_UA})
                    except Exception:
                        pass

                    sels = _selectors_for_url(url)

                    # Best-effort: wait a tiny bit in case the user just opened it.
                    try:
                        page.wait_for_load_state("domcontentloaded", timeout=5_000)
                    except Exception:
                        pass

                    # Cloudflare interstitial can resolve after a few seconds.
                    for _ in range(10):
                        try:
                            body_txt = (page.inner_text("body") or "")
                        except Exception:
                            body_txt = ""
                        b = body_txt.lower()
                        if body_txt and ("verifying you are human" not in b) and ("just a moment" not in b):
                            break
                        page.wait_for_timeout(800)

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
                    status = _classify_text(text)
                    out.append(OpenTabText(url=url, text=text, status=status, error=None))
                except PWTimeoutError as e:
                    out.append(OpenTabText(url=url, text="", status="error", error=f"timeout: {e}"))
                except Exception as e:
                    out.append(OpenTabText(url=url, text="", status="error", error=str(e)))

            return out
        except Exception:
            try:
                invalidate_cdp_browser()
            except Exception:
                pass
            return []


def open_urls_in_cdp(
    *,
    cdp_url: Optional[str],
    urls: list[str],
    max_open: int = 20,
    timeout_ms: int = 30_000,
    keep_existing: bool = True,
) -> int:
    """Open a list of URLs in the existing CDP Chrome session.

    This navigates by opening new tabs (pages) in the first browser context.
    It does not close any existing tabs by default.

    Returns the number of tabs opened.
    """

    if not cdp_url:
        return 0

    urls = [u for u in (urls or []) if _valid_http_url(u)]
    if max_open and len(urls) > max_open:
        urls = urls[:max_open]

    if not urls:
        return 0

    with _CDP_SEM:
        try:
            browser = get_cdp_browser(cdp_url, timeout_ms=max(timeout_ms, 45_000), retries=3, backoff_s=0.8)
            if browser is None:
                return 0

            ctx = browser.contexts[0] if browser.contexts else browser.new_context()

            opened = 0
            for u in urls:
                page = ctx.new_page()
                page.set_default_timeout(timeout_ms)
                try:
                    try:
                        page.set_extra_http_headers({"User-Agent": DEFAULT_UA})
                    except Exception:
                        pass
                    try:
                        page.goto(u, wait_until="domcontentloaded")
                    except Exception:
                        pass
                    opened += 1
                except Exception:
                    try:
                        page.close()
                    except Exception:
                        pass
            return opened
        except Exception:
            try:
                invalidate_cdp_browser()
            except Exception:
                pass
            return 0
