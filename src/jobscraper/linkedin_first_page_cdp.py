from __future__ import annotations

import json
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, List, Optional

from playwright.sync_api import TimeoutError as PWTimeoutError
from playwright.sync_api import sync_playwright

from .config import AppConfig


# LinkedIn uses multiple URL shapes:
# - /jobs/view/<slug>-<id>
# - /jobs/view/<id>
_JOB_ID_RE = re.compile(r"/jobs/view/(?:[^/?#]+-)?(\d+)")


@dataclass(frozen=True)
class LinkedInFirstPageConfig:
    url: str
    timeout_ms: int = 30_000
    out_json: Path = Path("data/linkedin_first_page.json")


def _pick_linkedin_page(pages, url_substr: str = "linkedin.com"):
    for p in pages:
        try:
            if url_substr in (p.url or ""):
                return p
        except Exception:
            continue
    return pages[0] if pages else None


def scrape_first_page_via_cdp(app_cfg: AppConfig, cfg: LinkedInFirstPageConfig) -> dict[str, Any]:
    """Scrape ONLY the first page of LinkedIn job search results via an existing CDP Chrome.

    Assumptions:
    - Chrome is already running with --remote-debugging-port
    - You are logged in to LinkedIn in that Chrome profile

    Returns a dict containing metadata + list of items.
    """

    with sync_playwright() as p:
        browser = p.chromium.connect_over_cdp(app_cfg.cdp_url)

        # Reuse an existing context if possible (keeps cookies/auth).
        ctx = browser.contexts[0] if browser.contexts else browser.new_context()

        page = _pick_linkedin_page(ctx.pages)
        if page is None:
            page = ctx.new_page()

        page.set_default_timeout(cfg.timeout_ms)

        # Navigate to the exact URL the user provided.
        try:
            page.goto(cfg.url, wait_until="domcontentloaded")
        except PWTimeoutError:
            # Sometimes LinkedIn keeps working even if domcontentloaded times out.
            pass

        # Let the page hydrate.
        page.wait_for_timeout(2000)

        # Try to ensure the results list is present.
        # LinkedIn UI varies; keep selectors broad.
        for sel in [
            "ul.scaffold-layout__list-container",
            "div.jobs-search-results-list",
            "main",
        ]:
            try:
                page.wait_for_selector(sel, timeout=6_000)
                break
            except PWTimeoutError:
                continue

        # Scroll the left results pane a bit to trigger lazy-loading of job cards.
        # No pagination; just load what belongs to the first page.
        page.evaluate(
            """
            () => {
              const candidates = [
                document.querySelector('div.scaffold-layout__list'),
                document.querySelector('div.jobs-search-results-list'),
                document.querySelector('div.scaffold-layout__list-container'),
              ].filter(Boolean);

              const scroller = candidates.find(el => el.scrollHeight > el.clientHeight) || candidates[0];
              if (!scroller) return;

              // Small progressive scrolls.
              const steps = [0.33, 0.66, 1.0];
              for (const t of steps) {
                scroller.scrollTop = Math.floor(scroller.scrollHeight * t);
              }
            }
            """
        )
        page.wait_for_timeout(800)

        items: List[Dict[str, Optional[str]]] = page.evaluate(
            """
            () => {
              const jobIdFromHref = (href) => {
                if (!href) return null;
                const m = href.match(/\/jobs\/view\/(?:[^/?#]+-)?(\d+)/);
                return m ? m[1] : null;
              };

              const norm = (s) => (s || '').replace(/\s+/g, ' ').trim();

              // Prefer anchors inside the results list.
              const root =
                document.querySelector('ul.scaffold-layout__list-container') ||
                document.querySelector('div.jobs-search-results-list') ||
                document;

              const anchors = Array.from(root.querySelectorAll('a[href*="/jobs/view/"]'));

              const out = [];
              const seen = new Set();

              for (const a of anchors) {
                const href = a.getAttribute('href') || '';
                const jobId = jobIdFromHref(href);
                if (!jobId || seen.has(jobId)) continue;

                // Card container
                const card = a.closest('li') || a.closest('div');

                const title = norm(
                  a.querySelector('span[aria-hidden="true"]')?.innerText ||
                  a.innerText ||
                  a.getAttribute('aria-label') ||
                  ''
                );

                // Company name appears in a few shapes depending on A/B tests.
                const company = norm(
                  // Current LinkedIn jobs list DOM often uses the Artdeco entity lockup subtitle.
                  card?.querySelector('.artdeco-entity-lockup__subtitle')?.innerText ||
                  // Fallbacks for other variants.
                  card?.querySelector('a[href*="/company/"]')?.innerText ||
                  card?.querySelector('a[href*="/school/"]')?.innerText ||
                  card?.querySelector('.job-card-container__primary-description')?.innerText ||
                  card?.querySelector('span.job-card-container__primary-description')?.innerText ||
                  card?.querySelector('.job-card-container__company-name')?.innerText ||
                  card?.querySelector('[class*="company-name"]')?.innerText ||
                  card?.querySelector('[data-company-name]')?.getAttribute('data-company-name') ||
                  ''
                );

                // Location is often the Artdeco caption, or a metadata item (may include remote/hybrid).
                const location = norm(
                  card?.querySelector('.artdeco-entity-lockup__caption')?.innerText ||
                  card?.querySelector('.job-card-container__metadata-item')?.innerText ||
                  card?.querySelector('li.job-card-container__metadata-item')?.innerText ||
                  card?.querySelector('[class*="metadata-item"]')?.innerText ||
                  card?.querySelector('.job-card-container__metadata-wrapper')?.innerText ||
                  ''
                );

                // LinkedIn sometimes uses relative hrefs.
                const jobUrl = href.startsWith('http') ? href : `https://www.linkedin.com${href}`;

                out.push({
                  jobId,
                  title: title || null,
                  company: company || null,
                  location: location || null,
                  jobUrl,
                });

                seen.add(jobId);
              }

              return out;
            }
            """
        )

        # If we got nothing, try a last-resort scan of full HTML.
        if not items:
            html = page.content()
            ids = list(dict.fromkeys(_JOB_ID_RE.findall(html)))
            items = [
                {
                    "jobId": jid,
                    "title": None,
                    "company": None,
                    "location": None,
                    "jobUrl": f"https://www.linkedin.com/jobs/view/{jid}/",
                }
                for jid in ids[:50]
            ]

        payload: Dict[str, Any] = {
            "source": "linkedin_first_page_cdp",
            "inputUrl": cfg.url,
            "finalUrl": page.url,
            "count": len(items),
            "items": items,
        }

        cfg.out_json.parent.mkdir(parents=True, exist_ok=True)
        cfg.out_json.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")

        # keep browser open (CDP-controlled Chrome), but close the Playwright connection
        browser.close()

        return payload
