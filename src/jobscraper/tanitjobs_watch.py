from __future__ import annotations

import argparse
import json
import re
from pathlib import Path
from typing import List, Optional, Tuple

from playwright.sync_api import TimeoutError as PWTimeoutError
from playwright.sync_api import sync_playwright

from jobscraper.filtering import is_relevant


DEFAULT_URL = "https://www.tanitjobs.com/"
# Tanitjobs job URLs look like: https://www.tanitjobs.com/job/1979371/sales-agent/
_JOB_RE = re.compile(r"/job/(\d+)(?:/|$)")


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

    with sync_playwright() as p:
        browser = None
        if cdp_url:
            browser = p.chromium.connect_over_cdp(cdp_url)
            ctx = browser.contexts[0] if browser.contexts else browser.new_context()
            page = ctx.new_page()
        elif user_data_dir:
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

        page.set_default_timeout(timeout_ms)

        try:
            page.goto(url, wait_until="domcontentloaded")
            page.wait_for_timeout(1500)

            title = page.title() or ""
            if "Just a moment" in title:
                return [], "blocked:cloudflare"

            items = page.eval_on_selector_all(
                "a[href*='/job/']",
                "els => els.map(a => ({href: a.getAttribute('href') || '', text: (a.textContent || '').trim()}))",
            )

            out: List[Tuple[str, str]] = []
            seen: set[str] = set()
            for it in items:
                href = (it.get("href") or "").strip()
                text = (it.get("text") or "").strip()
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

                out.append((jid, text or job_url))
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
        finally:
            try:
                page.close()
            except Exception:
                pass
            if not cdp_url:
                ctx.close()
            if browser and not cdp_url:
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
        return 1

    print(f"tanitjobs_watch: no new relevant (new_total={len(new_items)})")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
