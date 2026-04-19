from __future__ import annotations

import argparse
import datetime as dt
import re
from dataclasses import dataclass
from typing import Dict, Iterable, List, Optional, Tuple

from playwright.sync_api import TimeoutError as PWTimeoutError
from playwright.sync_api import sync_playwright

from jobscraper.db import JobDB
from jobscraper.filtering import is_relevant
from jobscraper.models import Job
from jobscraper.sheets_sync import SheetsConfig, append_jobs, ensure_jobs_header


_JOB_RE = re.compile(r"/job/(\d+)(?:/|$)")


@dataclass
class CatchupConfig:
    cdp_url: str
    start_url: str = "https://www.tanitjobs.com/jobs/"
    days: int = 3
    max_pages: int = 30
    timeout_ms: int = 30_000


def _now_utc() -> dt.datetime:
    return dt.datetime.now(dt.timezone.utc)


def _extract_jobs_from_page(page) -> List[Job]:
    """Extract job cards from the current listing page.

    We try to collect:
    - id from /job/<id>/
    - title from link text
    - posted date from nearby text (dd/mm/yyyy)

    Note: some top items can be promoted and may not show a date.
    """

    items = page.eval_on_selector_all(
        "a[href*='/job/']",
        """
        els => {
          const out = [];
          const dateRe = /\b\d{2}\/\d{2}\/\d{4}\b/;
          for (const a of els) {
            const href = a.getAttribute('href') || '';
            if (!href.includes('/job/')) continue;
            const text = (a.textContent || '').trim();

            // Walk up a few ancestors to find a container that contains a date.
            let node = a;
            let cardText = '';
            for (let i = 0; i < 6 && node; i++) {
              const t = (node.innerText || node.textContent || '').trim();
              if (t && (dateRe.test(t) || t.length > cardText.length)) {
                cardText = t;
              }
              node = node.parentElement;
            }

            out.push({ href, text, cardText });
          }
          return out;
        }
        """,
    )

    jobs: List[Job] = []
    seen: set[str] = set()
    for item in items:
        href = (item.get("href") or "").strip()
        text = (item.get("text") or "").strip()
        card_text = (item.get("cardText") or "").strip()

        m = _JOB_RE.search(href)
        if not m:
            continue
        jid = m.group(1)
        if jid in seen:
            continue
        seen.add(jid)

        if href.startswith("/"):
            url = "https://www.tanitjobs.com" + href
        elif href.startswith("http"):
            url = href
        else:
            url = "https://www.tanitjobs.com/" + href.lstrip("/")

        title = text or "(unknown)"

        posted_at = None
        dm = re.search(r"\b(\d{2})/(\d{2})/(\d{4})\b", card_text)
        if dm:
            dd, mm, yyyy = dm.group(1), dm.group(2), dm.group(3)
            try:
                posted_at = dt.datetime(int(yyyy), int(mm), int(dd), tzinfo=dt.timezone.utc)
            except Exception:
                posted_at = None

        # Avoid garbage titles like "2849 annonces trouvées".
        if "annonces trouv" in title.lower():
            title = "(unknown)"

        if title == "(unknown)":
            # Prefer deriving a title from the URL slug.
            # /job/<id>/<slug>/
            try:
                slug = url.split(f"/job/{jid}/", 1)[1].split("?", 1)[0].strip("/")
                if slug:
                    slug = slug.replace("-", " ")
                    title = slug
            except Exception:
                pass

        if title == "(unknown)" and card_text:
            # Fallback: pick the first meaningful line that isn't just the date.
            for line in [ln.strip() for ln in card_text.splitlines() if ln.strip()]:
                if re.fullmatch(r"\d{2}/\d{2}/\d{4}", line):
                    continue
                if "annonces trouv" in line.lower():
                    continue
                title = line
                break

        jobs.append(
            Job(
                source="tanitjobs",
                external_id=jid,
                title=title,
                company="",
                location="",
                url=url,
                posted_at=posted_at,
            )
        )

    return jobs


def _goto_next_page(page) -> bool:
    """Navigate to the next listing page.

    On Tanitjobs /jobs, pagination is often rendered as links like:
      ?searchId=...&action=search&page=2

    We resolve the current page number from the URL query (default 1) and then
    follow the link to page+1 if present.
    """

    cur_url = page.url or ""
    m = re.search(r"[?&]page=(\d+)", cur_url)
    cur_page = int(m.group(1)) if m else 1
    next_page = cur_page + 1

    # Try direct anchor for next page number.
    sel = f"a[href*='action=search'][href*='page={next_page}']"
    el = page.query_selector(sel)
    if el:
        href = el.get_attribute('href')
        if href:
            if href.startswith('?'):
                href = 'https://www.tanitjobs.com/jobs' + href
            elif href.startswith('/'):
                href = 'https://www.tanitjobs.com' + href
            page.goto(href, wait_until="domcontentloaded")
        else:
            el.click()
            page.wait_for_load_state("domcontentloaded")
        page.wait_for_timeout(800)
        return True

    # Fallback: sometimes there's a 'Suivant' link.
    for s in ["a:has-text('Suivant')", "a:has-text('Next')", "a[rel='next']", "a.page-numbers.next"]:
        try:
            el = page.query_selector(s)
            if el:
                href = el.get_attribute('href')
                if href:
                    page.goto(href, wait_until='domcontentloaded')
                else:
                    el.click()
                    page.wait_for_load_state('domcontentloaded')
                page.wait_for_timeout(800)
                return True
        except Exception:
            continue

    return False


def run_catchup(cfg: CatchupConfig) -> Tuple[int, int, int, List[Job]]:
    """Catch up by paging through listing pages.

    Returns: (scraped, new, relevant_new, relevant_new_jobs)

    Note: Tanitjobs listing pages sometimes hide dates for promoted listings,
    but normal listings contain a dd/mm/yyyy date which we parse.
    """

    db = JobDB("data/jobs.sqlite3")
    cutoff = _now_utc() - dt.timedelta(days=cfg.days)
    total_scraped = 0
    all_new: List[Job] = []

    with sync_playwright() as p:
        browser = p.chromium.connect_over_cdp(cfg.cdp_url)
        ctx = browser.contexts[0] if browser.contexts else browser.new_context()

        # Prefer reusing an existing already-cleared Tanitjobs tab.
        page = None
        for pg in ctx.pages:
            if pg.url.startswith("https://www.tanitjobs.com/jobs") or pg.url.startswith("https://www.tanitjobs.com/job"):
                page = pg
                break
        if page is None:
            page = ctx.new_page()

        page.set_default_timeout(cfg.timeout_ms)

        try:
            page.goto(cfg.start_url, wait_until="domcontentloaded")
            page.wait_for_timeout(1200)
            if "Just a moment" in (page.title() or ""):
                return 0, 0, 0, []

            for i in range(cfg.max_pages):
                jobs = _extract_jobs_from_page(page)
                total_scraped += len(jobs)

                # If the last dated job on the page is older than our cutoff, we can stop.
                dated = [j.posted_at for j in jobs if j.posted_at is not None]
                if dated:
                    oldest = min(dated)
                    if oldest < cutoff:
                        # Still upsert this page, but do not continue paging.
                        pass

                new_jobs = db.upsert_jobs(jobs)
                if new_jobs:
                    all_new.extend(new_jobs)

                if dated and min(dated) < cutoff:
                    break

                # If we've reached pages that contain nothing new, stop.
                if i > 0 and len(new_jobs) == 0:
                    break

                if not _goto_next_page(page):
                    break
        finally:
            # If we reused an existing user tab, don't close it.
            try:
                # ctx.pages always includes open pages; heuristic: only close if it's a blank/new tab.
                if (page.url or "").startswith("about:") or (page.url or "") == "":
                    page.close()
            except Exception:
                pass

    relevant_new_jobs = [j for j in all_new if is_relevant(j.title)]
    return total_scraped, len(all_new), len(relevant_new_jobs), relevant_new_jobs


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--cdp", required=True, help="CDP url, e.g. http://172.21.160.1:9330")
    ap.add_argument("--days", type=int, default=3)
    ap.add_argument("--max-pages", type=int, default=30)
    ap.add_argument("--start-url", default="https://www.tanitjobs.com/jobs/")
    ap.add_argument("--sheet-id", default="")
    ap.add_argument("--sheet-tab", default="Jobs")
    ap.add_argument("--sheet-account", default="wassimfekih3@gmail.com")
    args = ap.parse_args()

    cfg = CatchupConfig(
        cdp_url=args.cdp,
        days=args.days,
        max_pages=args.max_pages,
        start_url=args.start_url,
    )

    scraped, new, relevant_new, relevant_new_jobs = run_catchup(cfg)
    print(f"tanitjobs catchup: pages<=%d scraped=%d new=%d relevant_new=%d" % (cfg.max_pages, scraped, new, relevant_new))

    for j in relevant_new_jobs[:20]:
        print(f"NEW: {j.title} | {j.url}")

    if args.sheet_id:
        scfg = SheetsConfig(sheet_id=args.sheet_id, tab=args.sheet_tab, account=args.sheet_account)
        ensure_jobs_header(scfg)
        append_jobs(scfg, relevant_new_jobs, date_label=f"catchup_{args.days}d")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
