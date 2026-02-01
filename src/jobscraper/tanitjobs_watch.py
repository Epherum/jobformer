from __future__ import annotations

import argparse
import json
import re
from pathlib import Path
from typing import Optional, Tuple

from playwright.sync_api import TimeoutError as PWTimeoutError
from playwright.sync_api import sync_playwright


DEFAULT_URL = "https://www.tanitjobs.com/"
# Tanitjobs job URLs look like: https://www.tanitjobs.com/job/1979371/sales-agent/
_JOB_RE = re.compile(r"/job/(\d+)(?:/|$)")


def fetch_first_job_id(
    url: str,
    *,
    user_data_dir: Optional[str],
    headless: bool,
    timeout_ms: int,
    cdp_url: Optional[str] = None,
) -> Tuple[Optional[str], str]:
    with sync_playwright() as p:
        browser = None
        if cdp_url:
            # Connect to an already-running Chrome/Edge started with --remote-debugging-port.
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
                return None, "blocked:cloudflare"

            # grab first /job/<id>/ link
            hrefs = page.eval_on_selector_all(
                "a[href*='/job/']",
                "els => els.map(e => e.getAttribute('href')).filter(Boolean)",
            )
            for href in hrefs:
                m = _JOB_RE.search(href)
                if m:
                    return m.group(1), "ok"

            body = (page.inner_text("body") or "")[:8000]
            if "Verify you are human" in body or "Cloudflare" in body:
                return None, "blocked:cloudflare"

            return None, "no_job_ids_found"
        except PWTimeoutError:
            return None, "timeout"
        finally:
            # For CDP mode, don't close the shared context/browser; just close the page.
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

    jid, reason = fetch_first_job_id(
        args.url,
        user_data_dir=user_data_dir,
        headless=(not args.headed),
        timeout_ms=args.timeout_ms,
        cdp_url=cdp_url,
    )

    if not jid:
        print(f"tanitjobs_watch: no job id ({reason})")
        return 2

    last = state.get("last_job_id")
    if last is None:
        state["last_job_id"] = jid
        save_state(state_path, state)
        print(f"tanitjobs_watch: initialized last_job_id={jid}")
        return 0

    if str(jid) != str(last):
        state["last_job_id"] = jid
        save_state(state_path, state)
        print(f"tanitjobs_watch: NEW first job id {jid} (was {last})")
        print(f"url: {args.url}")
        return 1

    print(f"tanitjobs_watch: unchanged ({jid})")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
