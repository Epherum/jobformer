from __future__ import annotations

import argparse
import json
import re
from pathlib import Path
from typing import List, Optional, Tuple
from urllib.parse import unquote, urlparse

from playwright.sync_api import TimeoutError as PWTimeoutError
from playwright.sync_api import sync_playwright

from jobscraper.cdp_session import get_cdp_browser, invalidate_cdp_browser
from jobscraper.filtering import is_relevant
from jobscraper.alerts.ntfy import send_many


DEFAULT_URL = "https://www.tanitjobs.com/"
# Tanitjobs job URLs look like: https://www.tanitjobs.com/job/1979371/sales-agent/
_JOB_RE = re.compile(r"/job/(\d+)(?:/|$)")


def _title_from_job_url(job_url: str) -> str:
    """Best-effort title from a Tanitjobs job URL.

    Example: /job/1971667/charge-e-de-recouvrement-clients-facturation/
    -> "charge e de recouvrement clients facturation"

    Keeps it simple (no smart casing). Only used as a fallback when DOM has no text.
    """
    try:
        p = urlparse(job_url)
        parts = [x for x in p.path.split("/") if x]
        # expected: ['job', '<id>', '<slug>', ...]
        if len(parts) >= 3 and parts[0] == "job":
            slug = unquote(parts[2])
            slug = slug.replace("-", " ").replace("_", " ").strip()
            slug = re.sub(r"\s+", " ", slug)
            if slug and slug != parts[1]:
                return slug
    except Exception:
        pass
    return ""


def fetch_first_page_jobs(
    url: str,
    *,
    user_data_dir: Optional[str],
    headless: bool,
    timeout_ms: int,
    cdp_url: Optional[str] = None,
    max_jobs: int = 80,
) -> Tuple[List[Tuple[str, str]], str]:
    """Return list of (job_id, title) from the first page."""

    def _scrape(page) -> Tuple[List[Tuple[str, str]], str]:
        page.set_default_timeout(timeout_ms)

        try:
            page.goto(url, wait_until="domcontentloaded")
            page.wait_for_timeout(1500)

            title = page.title() or ""
            if "Just a moment" in title:
                return [], "blocked:cloudflare"

            items = page.eval_on_selector_all(
                "a[href*='/job/']",
                """
                els => els.map(a => ({
                  href: a.getAttribute('href') || '',
                  // innerText is closer to what a human sees than textContent
                  text: (a.innerText || '').trim(),
                  aria: (a.getAttribute('aria-label') || '').trim(),
                  title: (a.getAttribute('title') || '').trim(),
                  // sometimes the title sits on a parent container
                  cardText: ((a.closest('article') || a.closest('div') || a).innerText || '').trim(),
                }))
                """,
            )

            out: List[Tuple[str, str]] = []
            seen: set[str] = set()
            for it in items:
                href = (it.get("href") or "").strip()
                text = (it.get("text") or "").strip()
                aria = (it.get("aria") or "").strip()
                title_attr = (it.get("title") or "").strip()
                card_text = (it.get("cardText") or "").strip()

                m = _JOB_RE.search(href)
                if not m:
                    continue
                jid = m.group(1)
                if jid in seen:
                    continue
                seen.add(jid)

                # normalize URL
                if href.startswith("/"):
                    job_url = "https://www.tanitjobs.com" + href
                elif href.startswith("http"):
                    job_url = href
                else:
                    job_url = "https://www.tanitjobs.com/" + href.lstrip("/")

                # Prefer visible title text; fallback progressively.
                title = text or aria or title_attr
                if not title and card_text:
                    # Use first non-empty line from the card container.
                    for ln in (card_text.splitlines() if card_text else []):
                        ln = ln.strip()
                        if ln and len(ln) >= 3:
                            title = ln
                            break

                if not title:
                    title = _title_from_job_url(job_url)

                out.append((jid, title or job_url))
                if len(out) >= max_jobs:
                    break

            if not out:
                body = (page.inner_text("body") or "")[:8000]
                if "Verify you are human" in body or "Cloudflare" in body:
                    return [], "blocked:cloudflare"
                return [], "no_job_ids_found"

            return out, "ok"
        except PWTimeoutError:
            return [], "timeout"

    if cdp_url:
        try:
            browser = get_cdp_browser(
                cdp_url,
                timeout_ms=timeout_ms,
                retries=2,
                backoff_s=0.8,
                raise_on_fail=True,
            )
        except RuntimeError as e:
            return [], f"cdp_error: {e}"

        ctx = browser.contexts[0] if browser.contexts else browser.new_context()
        page = ctx.new_page()
        try:
            return _scrape(page)
        except Exception:
            invalidate_cdp_browser()
            raise
        finally:
            try:
                page.close()
            except Exception:
                pass

    with sync_playwright() as p:
        browser = None
        if user_data_dir:
            ctx = p.chromium.launch_persistent_context(
                user_data_dir,
                headless=headless,
                viewport={"width": 1280, "height": 900},
                locale="fr-FR",
            )
            page = ctx.new_page()
        else:
            browser = p.chromium.launch(headless=headless)
            ctx = browser.new_context(viewport={"width": 1280, "height": 900}, locale="fr-FR")
            page = ctx.new_page()

        try:
            return _scrape(page)
        finally:
            try:
                page.close()
            except Exception:
                pass
            ctx.close()
            if browser:
                browser.close()


def load_state(path: Path) -> dict:
    if not path.exists():
        return {}
    return json.loads(path.read_text(encoding="utf-8"))


def save_state(path: Path, state: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(state, ensure_ascii=False, indent=2), encoding="utf-8")


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--url", default=DEFAULT_URL)
    ap.add_argument("--state", default="data/tanitjobs_state.json")
    ap.add_argument("--max-jobs", type=int, default=80)
    ap.add_argument(
        "--user-data-dir",
        default="data/tanitjobs_chrome_profile",
        help="Persistent browser profile dir. Use to keep CF clearance cookies.",
    )
    ap.add_argument("--no-profile", action="store_true", help="Run without persistent profile (likely blocked).")
    ap.add_argument("--headed", action="store_true", help="Run with a visible browser window (useful for first run to solve CF).")
    ap.add_argument("--timeout-ms", type=int, default=30_000)
    ap.add_argument(
        "--cdp",
        default="",
        help="CDP URL to control an existing Chrome/Edge (e.g. http://127.0.0.1:9222). Overrides profile/headless.",
    )
    args = ap.parse_args()

    state_path = Path(args.state)
    state = load_state(state_path)

    user_data_dir = None if args.no_profile else args.user_data_dir
    cdp_url = args.cdp.strip() or None

    jobs, reason = fetch_first_page_jobs(
        args.url,
        user_data_dir=user_data_dir,
        headless=(not args.headed),
        timeout_ms=args.timeout_ms,
        cdp_url=cdp_url,
        max_jobs=args.max_jobs,
    )

    if not jobs:
        print(f"tanitjobs_watch: no jobs ({reason})")
        return 2

    current_ids = [jid for jid, _ in jobs]
    prev_ids = state.get("seen_ids")

    if prev_ids is None:
        state["seen_ids"] = current_ids
        save_state(state_path, state)
        print(f"tanitjobs_watch: initialized seen_ids={len(current_ids)}")
        return 0

    prev_set = set(prev_ids)
    new_items = [(jid, title) for jid, title in jobs if jid not in prev_set]
    new_relevant = [(jid, title) for jid, title in new_items if is_relevant(title)]

    state["seen_ids"] = current_ids
    save_state(state_path, state)

    if new_relevant:
        print(f"tanitjobs_watch: NEW relevant={len(new_relevant)} (new_total={len(new_items)})")
        for jid, title in new_relevant[:10]:
            print(f"NEW: {title} | https://www.tanitjobs.com/job/{jid}/")

        lines = [f"{title} | https://www.tanitjobs.com/job/{jid}/" for jid, title in new_relevant]
        send_many(
            title=f"Tanitjobs: {len(new_relevant)} new relevant",
            lines=lines,
            tags=["briefcase"],
            priority=4,
        )
        return 1

    print(f"tanitjobs_watch: no new relevant (new_total={len(new_items)})")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
