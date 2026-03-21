from __future__ import annotations

import re
import threading
from typing import Optional

from .cdp_session import get_cdp_browser, invalidate_cdp_browser

_CDP_SEM = threading.Semaphore(1)
_JOB_RE = re.compile(r"/job/(\d+)(?:/|$)")


def _clean_text(text: str) -> str:
    return re.sub(r"\s+", " ", text or "").strip()


def _extract_job_id(url: str) -> str:
    m = _JOB_RE.search(url or "")
    return m.group(1) if m else ""


def _pick_jobs_page(ctx):
    for p in ctx.pages:
        try:
            u = p.url or ""
            if "tanitjobs.com/jobs" in u:
                return p
        except Exception:
            continue
    return None


def fetch_tanitjobs_page_text(
    url: str,
    cdp_url: Optional[str],
    timeout_ms: int = 45_000,
    max_chars: int = 8000,
) -> str:
    """Fetch Tanitjobs text strictly from the /jobs listing page card.

    Important rule: NEVER navigate to a detail page. We only inspect an already-open
    Tanitjobs /jobs listing tab in the shared CDP browser and extract the matching
    card text for the job id. If the listing tab is not open or the card is not found,
    return empty text.
    """

    if not url or not cdp_url:
        return ""

    job_id = _extract_job_id(url)
    if not job_id:
        return ""

    with _CDP_SEM:
        try:
            browser = get_cdp_browser(cdp_url, timeout_ms=max(timeout_ms, 20_000), retries=3, backoff_s=0.8, raise_on_fail=False)
            if browser is None:
                return ""
            ctx = browser.contexts[0] if browser.contexts else browser.new_context()
            page = _pick_jobs_page(ctx)
            if page is None:
                return ""

            page.set_default_timeout(timeout_ms)
            page.wait_for_timeout(500)

            text = page.evaluate(
                """(jobId) => {
                  const norm = (s) => (s || '').replace(/\s+/g, ' ').trim();
                  const anchors = Array.from(document.querySelectorAll(`a[href*='/job/${jobId}']`));
                  for (const a of anchors) {
                    const card = a.closest('article.listing-item, article[class*="listing-item"], .listing-item, article, .card, .job-item, .listing-item, li, .list-group-item, .ais-Hits-item, div');
                    if (!card) continue;
                    const title = norm(card.querySelector('.listing-item__title a, .listing-item__title')?.innerText || '');
                    const company = norm(card.querySelector('.listing-item-info-company')?.innerText || '').replace(/\s+-\s*$/, '');
                    const location = norm(card.querySelector('.listing-item-info-location')?.innerText || '');
                    const desc = norm(card.querySelector('.listing-item__desc.hidden-sm.hidden-xs, .listing-item__desc')?.innerText || '');
                    const date = norm(card.querySelector('.listing-item__date')?.innerText || '');
                    const parts = [title, [company, location].filter(Boolean).join(' - '), desc, date].filter(Boolean);
                    const txt = norm(parts.join('\n'));
                    if (txt && txt.length > 30) return txt;
                    const fallback = norm(card.innerText || '');
                    if (fallback && fallback.length > 80) return fallback;
                  }
                  return '';
                }""",
                job_id,
            )

            text = _clean_text(text or "")
            if max_chars and len(text) > max_chars:
                text = text[:max_chars]
            return text
        except Exception:
            try:
                invalidate_cdp_browser()
            except Exception:
                pass
            return ""
