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
    """Best-effort extraction.

    Because we cannot reliably inspect Tanitjobs DOM until Cloudflare is passed,
    this function tries multiple heuristics and writes HTML snapshots for tuning.
    """

    # Heuristic 1: job cards often contain anchors to a job details page.
    anchors = page.locator("a[href]")
    count = anchors.count()

    jobs: List[Job] = []
    seen = set()

    for i in range(min(count, 500)):
        a = anchors.nth(i)
        href = a.get_attribute("href")
        if not href:
            continue
        if "tanitjobs.com" not in href and not href.startswith("/"):
            continue

        url = urljoin(page.url, href)
        # Filter out obvious non-job links.
        if any(x in url for x in ("/login", "/register", "/contact", "/privacy", "/terms")):
            continue
        if url.rstrip("/") == "https://www.tanitjobs.com" or url.rstrip("/") == "https://www.tanitjobs.com/jobs":
            continue

        # Many job links contain /jobs/ or /job/
        if "/job" not in url and "/jobs/" not in url:
            continue

        key = url
        if key in seen:
            continue
        seen.add(key)

        title = (a.inner_text() or "").strip()
        if not title or len(title) < 3:
            # Try aria-label/title attributes.
            title = (a.get_attribute("aria-label") or a.get_attribute("title") or "").strip()

        # Company/location are usually not in the <a> itself. Leave blank for now.
        jobs.append(
            Job(
                source="tanitjobs",
                external_id=_guess_external_id(url),
                title=title or "(unknown)",
                company="",
                location="",
                url=url,
                posted_at=None,
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

            # Save HTML for debugging/tuning selectors.
            Path("debug").mkdir(exist_ok=True)
            html_path = Path("debug") / "tanitjobs_last.html"
            html_path.write_text(page.content(), encoding="utf-8")

            return _extract_jobs(page)
        finally:
            ctx.close()
