from __future__ import annotations

import atexit
import threading
import time
from typing import Optional

from playwright.sync_api import Browser, sync_playwright


_LOCK = threading.Lock()
_PLAYWRIGHT = None
_BROWSER: Optional[Browser] = None
_CDP_URL: Optional[str] = None


def _shutdown() -> None:
    global _PLAYWRIGHT, _BROWSER, _CDP_URL
    try:
        if _BROWSER is not None:
            _BROWSER.close()
    except Exception:
        pass
    _BROWSER = None
    _CDP_URL = None
    try:
        if _PLAYWRIGHT is not None:
            _PLAYWRIGHT.stop()
    except Exception:
        pass
    _PLAYWRIGHT = None


def _ensure_playwright():
    global _PLAYWRIGHT
    if _PLAYWRIGHT is None:
        _PLAYWRIGHT = sync_playwright().start()
        atexit.register(_shutdown)


def invalidate_cdp_browser() -> None:
    global _BROWSER, _CDP_URL
    with _LOCK:
        try:
            if _BROWSER is not None:
                _BROWSER.close()
        except Exception:
            pass
        _BROWSER = None
        _CDP_URL = None


def get_cdp_browser(
    cdp_url: str,
    *,
    timeout_ms: int = 45_000,
    retries: int = 3,
    backoff_s: float = 0.8,
    raise_on_fail: bool = False,
) -> Optional[Browser]:
    if not cdp_url:
        return None

    global _BROWSER, _CDP_URL

    with _LOCK:
        if _BROWSER is not None and _CDP_URL == cdp_url:
            return _BROWSER

        # Reset if URL changed or browser is stale.
        try:
            if _BROWSER is not None:
                _BROWSER.close()
        except Exception:
            pass
        _BROWSER = None
        _CDP_URL = None

        _ensure_playwright()

        last_err: Optional[Exception] = None
        for attempt in range(retries):
            try:
                browser = _PLAYWRIGHT.chromium.connect_over_cdp(cdp_url, timeout=timeout_ms)
                _BROWSER = browser
                _CDP_URL = cdp_url
                return browser
            except Exception as e:  # pragma: no cover - best effort
                last_err = e
                time.sleep(backoff_s * (2**attempt))

        # If all retries failed, leave browser unset.
        if last_err:
            if raise_on_fail:
                msg = (
                    f"Failed to connect to CDP at {cdp_url} after {retries} attempts. "
                    "Try restarting Chrome/Edge with --remote-debugging-port and ensure /json/version is reachable."
                )
                raise RuntimeError(msg) from last_err
        return None
