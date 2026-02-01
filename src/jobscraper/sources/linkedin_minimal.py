from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Optional, Tuple

from playwright.sync_api import TimeoutError as PWTimeoutError
from playwright.sync_api import sync_playwright


LINKEDIN_TUNISIA_LAST_2H = "https://www.linkedin.com/jobs/search/?geoId=102134353&f_TPR=r7200&sortBy=DD"

# LinkedIn uses multiple URL shapes:
# - /jobs/view/<slug>-<id>
# - /jobs/view/<id>
_JOB_ID_RE = re.compile(r"/jobs/view/(?:[^/?#]+-)?(\d+)")


@dataclass
class LinkedInMinimalConfig:
    url: str = LINKEDIN_TUNISIA_LAST_2H
    timeout_ms: int = 30_000
    user_agent: str = (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/120.0.0.0 Safari/537.36"
    )


def fetch_first_job_id(cfg: Optional[LinkedInMinimalConfig] = None) -> Tuple[Optional[str], str]:
    """Fetch the first job id visible on the search results page.

    Returns: (job_id, debug_reason)
    """
    cfg = cfg or LinkedInMinimalConfig()

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        ctx = browser.new_context(user_agent=cfg.user_agent)
        page = ctx.new_page()
        page.set_default_timeout(cfg.timeout_ms)

        try:
            page.goto(cfg.url, wait_until="domcontentloaded")
            # give the page a moment to hydrate
            page.wait_for_timeout(1500)

            # If LinkedIn shows auth wall or challenge, the HTML won't contain job links.
            html = page.content()
            m = _JOB_ID_RE.search(html)
            if m:
                return m.group(1), "ok"

            # Fallback: scan all anchors hrefs.
            hrefs = page.eval_on_selector_all(
                "a[href]",
                "els => els.map(e => e.getAttribute('href')).filter(Boolean)",
            )
            for href in hrefs:
                mm = _JOB_ID_RE.search(href)
                if mm:
                    return mm.group(1), "ok(from_hrefs)"

            # Detect common blocks
            txt = (page.inner_text("body") or "")[:8000]
            if "Verify" in txt and "human" in txt:
                return None, "blocked:verify_human"
            if "Sign in" in txt and "LinkedIn" in txt:
                # could be normal header too, but often indicates auth gate
                return None, "no_job_ids:maybe_auth_wall"

            return None, "no_job_ids_found"
        except PWTimeoutError:
            return None, "timeout"
        finally:
            ctx.close()
            browser.close()
