from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import List, Optional
from urllib.parse import urljoin, urlparse, parse_qs

import os
import platform

from playwright.sync_api import BrowserContext, Page, sync_playwright

from ..models import Job


DEFAULT_SEARCH_URL = "https://www.tanitjobs.com/jobs/"  # you will likely add query params


def _guess_external_id(url: str) -> str:
    # Tanitjobs URLs vary. We try common patterns:
    # - query param currentJobId=...
    # - numeric slug segments
    parsed = urlparse(url)
    q = parse_qs(parsed.query)
    for key in ("job_id", "currentJobId", "id"):
        if key in q and q[key]:
            return q[key][0]

    # fallback: path
    path = parsed.path.strip("/")
    if not path:
        return url
    return path


@dataclass
class TanitjobsConfig:
    search_url: str = DEFAULT_SEARCH_URL
    user_data_dir: str = "state/tanitjobs"
    timeout_ms: int = 45_000
    browser_channel: Optional[str] = None  # e.g. "msedge" on Windows


def _open_page(ctx: BrowserContext, url: str, timeout_ms: int) -> Page:
    page = ctx.new_page()
    page.set_default_timeout(timeout_ms)
    page.goto(url, wait_until="domcontentloaded")
    return page


def _extract_jobs(page: Page) -> List[Job]:
    """Extract Tanitjobs listing cards with title, company, location, and date.

    The /jobs page exposes cards as article.listing-item blocks. We read the card
    fields directly instead of relying on bare anchors so we keep the richer data
    shown on the listing page.
    """

    items = page.eval_on_selector_all(
        "article.listing-item, article[class*='listing-item']",
        """(cards) => cards.map((card) => {
            const norm = (s) => (s || '').replace(/\s+/g, ' ').trim();
            const href = card.querySelector(".listing-item__title a[href*='/job/'], a[href*='/job/']")?.href || '';
            const title = norm(card.querySelector('.listing-item__title a, .listing-item__title')?.innerText || '');
            const company = norm(card.querySelector('.listing-item-info-company')?.innerText || '').replace(/\s+-\s*$/, '');
            const location = norm(card.querySelector('.listing-item-info-location')?.innerText || '');
            const postedAt = norm(card.querySelector('.listing-item__date')?.innerText || '');
            const cardText = norm(card.innerText || '');
            return { href, title, company, location, postedAt, cardText };
        })""",
    )

    jobs: List[Job] = []
    seen = set()

    for item in items or []:
        href = (item.get("href") or "").strip()
        if not href:
            continue
        url = urljoin(page.url, href)
        if any(x in url for x in ("/login", "/register", "/contact", "/privacy", "/terms")):
            continue
        if url.rstrip("/") == "https://www.tanitjobs.com" or url.rstrip("/") == "https://www.tanitjobs.com/jobs":
            continue
        if "/job" not in url and "/jobs/" not in url:
            continue
        if url in seen:
            continue
        seen.add(url)

        title = (item.get("title") or "").strip()
        company = (item.get("company") or "").strip()
        location = (item.get("location") or "").strip()
        posted_at = (item.get("postedAt") or "").strip() or None
        if not title:
            card_text = (item.get("cardText") or "").strip()
            if card_text:
                title = card_text.split(' Voir Plus', 1)[0].split('  ', 1)[0].strip()

        jobs.append(
            Job(
                source="tanitjobs",
                external_id=_guess_external_id(url),
                title=title or "(unknown)",
                company=company,
                location=location,
                url=url,
                posted_at=posted_at,
            )
        )

    return jobs


def scrape_tanitjobs(cfg: Optional[TanitjobsConfig] = None, headed: bool = False) -> List[Job]:
    cfg = cfg or TanitjobsConfig()

    Path(cfg.user_data_dir).mkdir(parents=True, exist_ok=True)

    with sync_playwright() as p:
        launch_kwargs = {
            "user_data_dir": cfg.user_data_dir,
            "headless": not headed,
            "viewport": {"width": 1280, "height": 900},
        }

        # Prefer a real installed browser on Windows to reduce bot detection.
        # If browser_channel is not explicitly set, default to msedge on Windows.
        channel = cfg.browser_channel
        if channel is None and platform.system().lower().startswith("win"):
            channel = "msedge"
        if channel:
            launch_kwargs["channel"] = channel

        ctx = p.chromium.launch_persistent_context(**launch_kwargs)
        try:
            page = _open_page(ctx, cfg.search_url, cfg.timeout_ms)

            # Give redirects/challenges a moment.
            page.wait_for_timeout(2500)

            # In headed mode, keep the window open for a bit so you can complete
            # Cloudflare / adjust filters manually. Then we snapshot + extract.
            if headed:
                print("\n[tanitjobs] Headed mode: complete Cloudflare in the browser if it appears.")
                print("[tanitjobs] Waiting 120 seconds before scraping...\n")
                page.wait_for_timeout(120_000)

            jobs = _extract_jobs(page)

            # Save HTML for debugging/tuning selectors only when needed.
            # Enable explicitly with DEBUG_SNAPSHOTS=1, or when scraping returns 0 jobs.
            debug_snapshots = (os.getenv("DEBUG_SNAPSHOTS") or "").strip() == "1"
            if debug_snapshots or (len(jobs) == 0):
                Path("debug").mkdir(exist_ok=True)
                html_path = Path("debug") / "tanitjobs_last.html"
                html_path.write_text(page.content(), encoding="utf-8")

            return jobs
        finally:
            ctx.close()
