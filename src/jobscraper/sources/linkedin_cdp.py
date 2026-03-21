from __future__ import annotations

import datetime as dt
from dataclasses import dataclass
from typing import List, Tuple

from playwright.sync_api import TimeoutError as PWTimeoutError

from ..cdp_session import get_cdp_browser, invalidate_cdp_browser
from ..models import Job


@dataclass(frozen=True)
class LinkedInCDPConfig:
    cdp_url: str
    url: str
    timeout_ms: int = 70_000
    max_jobs: int = 60


def _clean_title(title: str) -> str:
    t = " ".join((title or "").split()).strip()
    if not t:
        return "(unknown)"

    # Sometimes LinkedIn repeats the title twice in the anchor text.
    parts = t.split(" ")
    if len(parts) >= 6 and len(parts) % 2 == 0:
        half = len(parts) // 2
        if parts[:half] == parts[half:]:
            t = " ".join(parts[:half])

    # Drop noisy suffix.
    t = t.replace(" with verification", "").strip()
    return t or "(unknown)"


def scrape_linkedin_first_page(cfg: LinkedInCDPConfig) -> Tuple[List[Job], str]:
    """Scrape the first page of a LinkedIn jobs search via existing Chrome CDP.

    No pagination. Best-effort extraction of title/company/location/jobUrl.
    """

    jobs: List[Job] = []

    try:
        browser = get_cdp_browser(
            cfg.cdp_url,
            timeout_ms=cfg.timeout_ms,
            retries=2,
            backoff_s=0.8,
            raise_on_fail=True,
        )
    except RuntimeError as e:
        return [], f"cdp_error: {e}"

    ctx = browser.contexts[0] if browser.contexts else browser.new_context()
    page = None
    created = False
    for pg in ctx.pages:
        try:
            if "linkedin.com/jobs" in (pg.url or ""):
                page = pg
                break
        except Exception:
            continue
    if page is None:
        page = ctx.new_page()
        created = True
    page.set_default_timeout(cfg.timeout_ms)

    try:
        last_nav_err = None
        for attempt in range(3):
            try:
                page.goto(cfg.url, wait_until="domcontentloaded", timeout=cfg.timeout_ms)
                last_nav_err = None
                break
            except PWTimeoutError as e:
                last_nav_err = e
                try:
                    page.wait_for_load_state("domcontentloaded", timeout=10_000)
                    last_nav_err = None
                    break
                except Exception:
                    pass
            except Exception as e:
                last_nav_err = e
            page.wait_for_timeout(1500 * (attempt + 1))

        page.wait_for_timeout(3500)

        ready = False
        for _ in range(3):
            for sel in [
                "ul.scaffold-layout__list-container",
                "div.jobs-search-results-list",
                "main",
            ]:
                try:
                    page.wait_for_selector(sel, timeout=10_000)
                    ready = True
                    break
                except PWTimeoutError:
                    continue
            if ready:
                break
            page.wait_for_timeout(2000)

        # Scroll a bit to load the visible first page cards.
        page.evaluate(
            """
            () => {
              const candidates = [
                document.querySelector('div.scaffold-layout__list'),
                document.querySelector('div.jobs-search-results-list'),
                document.querySelector('ul.scaffold-layout__list-container')?.parentElement,
              ].filter(Boolean);

              const scroller = candidates.find(el => el.scrollHeight > el.clientHeight) || candidates[0];
              if (!scroller) return;
              const steps = [0.25, 0.6, 0.95];
              for (const t of steps) {
                scroller.scrollTop = Math.floor(scroller.scrollHeight * t);
              }
            }
            """
        )
        page.wait_for_timeout(900)

        items = page.evaluate(
            """
            () => {
              const norm = (s) => (s || '').replace(/\s+/g, ' ').trim();
              const jobIdFromHref = (href) => {
                if (!href) return null;
                const m = href.match(/\/jobs\/view\/(?:[^/?#]+-)?(\d+)/);
                return m ? m[1] : null;
              };

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

                const card = a.closest('li') || a.closest('div');

                // Title: prefer aria-hidden span (usually the clean title line).
                const title = norm(
                  a.querySelector('span[aria-hidden="true"]')?.innerText ||
                  a.innerText ||
                  a.getAttribute('aria-label') ||
                  ''
                );

                // Company: prefer a company/school link inside the card.
                const company = norm(
                  card?.querySelector('.artdeco-entity-lockup__subtitle')?.innerText ||
                  card?.querySelector('a[href*="/company/"]')?.innerText ||
                  card?.querySelector('a[href*="/school/"]')?.innerText ||
                  card?.querySelector('.job-card-container__primary-description')?.innerText ||
                  card?.querySelector('span.job-card-container__primary-description')?.innerText ||
                  card?.querySelector('.job-card-container__company-name')?.innerText ||
                  ''
                );

                const location = norm(
                  card?.querySelector('.artdeco-entity-lockup__caption')?.innerText ||
                  card?.querySelector('.job-card-container__metadata-item')?.innerText ||
                  card?.querySelector('li.job-card-container__metadata-item')?.innerText ||
                  card?.querySelector('[class*="metadata-item"]')?.innerText ||
                  card?.querySelector('.job-card-container__metadata-wrapper')?.innerText ||
                  ''
                );

                const jobUrl = href.startsWith('http') ? href : `https://www.linkedin.com${href}`;

                out.push({ jobId, title, company, location, jobUrl });
                seen.add(jobId);

                if (out.length >= 80) break;
              }

              return out;
            }
            """
        )

        if not items and last_nav_err is not None:
            raise last_nav_err

        for it in items[: cfg.max_jobs]:
            job_id = (it.get("jobId") or "").strip()
            if not job_id:
                continue

            jobs.append(
                Job(
                    source="linkedin",
                    external_id=str(job_id),
                    title=_clean_title(it.get("title") or ""),
                    company=(it.get("company") or "").strip(),
                    location=(it.get("location") or "").strip(),
                    url=(it.get("jobUrl") or "").strip(),
                    posted_at=None,
                )
            )

        return jobs, "cdp_first_page"
    except Exception:
        invalidate_cdp_browser()
        raise
    finally:
        try:
            if created:
                page.close()
        except Exception:
            pass
