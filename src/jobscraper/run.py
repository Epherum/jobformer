from __future__ import annotations

import argparse
import datetime as dt
import os
from dataclasses import replace
from pathlib import Path

from .db import JobDB

from .sources.tanitjobs import TanitjobsConfig, scrape_tanitjobs
from .tanitjobs_watch import fetch_first_page_jobs
from .sources.keejob import KeejobConfig, scrape_keejob
from .sources.wttj import WTTJConfig, scrape_wttj
from .sources.weworkremotely import WWRConfig, scrape_weworkremotely
from .sources.remoteok import RemoteOKConfig, scrape_remoteok
from .sources.remotive import RemotiveConfig, scrape_remotive
from .sources.linkedin_cdp import LinkedInCDPConfig, scrape_linkedin_first_page
from .filtering import is_relevant, is_english_title
from .sheets_sync import SheetsConfig, append_jobs, ensure_jobs_header, append_jobs_routed
from .job_text_cache_db import JobTextCacheDB
from .url_canon import canonicalize_url
from .alerts.pushover import send_summary


def main() -> int:
    # Load data/config.env into os.environ (so env-only config like LINKEDIN_URLS works
    # when running this module directly).
    try:
        from .config import load_config

        cfg = load_config()
        try:
            os.chdir(cfg.base_dir)
        except Exception:
            pass
    except Exception:
        pass

    p = argparse.ArgumentParser()
    p.add_argument(
        "--source",
        choices=["tanitjobs", "keejob", "welcometothejungle", "weworkremotely", "remoteok", "remotive", "linkedin"],
        required=True,
    )
    p.add_argument("--once", action="store_true", help="Run once and exit")
    p.add_argument("--headed", action="store_true", help="Run with a visible browser (needed to solve Cloudflare)")
    p.add_argument(
        "--tanitjobs-url",
        default=None,
        help="Override Tanitjobs search URL (include your query params)",
    )
    p.add_argument(
        "--linkedin-url",
        default=None,
        help="Override LinkedIn search URL (defaults to env LINKEDIN_URL).",
    )
    p.add_argument(
        "--browser-channel",
        default=None,
        help="Playwright browser channel (e.g. msedge, chrome). Useful on Windows.",
    )
    p.add_argument(
        "--user-data-dir",
        default=None,
        help="Persistent browser profile directory. Use with care; on Windows Edge it's typically %%LOCALAPPDATA%%\\Microsoft\\Edge\\User Data. Close Edge first.",
    )
    p.add_argument("--sheet-id", default=None, help="Google Sheet ID to append new relevant jobs to")
    p.add_argument("--notify", action="store_true", help="Send Pushover notification when relevant_new > 0")
    p.add_argument("--sheet-tab", default="Jobs", help="Sheet tab name")
    p.add_argument("--sheet-account", default="wassimfekih2@gmail.com", help="gog account email")
    args = p.parse_args()

    db = JobDB(Path("data") / "jobs.sqlite3")

    # One consistent date label for all sources (local day).
    today_label = dt.date.today().isoformat()

    if args.source == "tanitjobs":
        # Prefer CDP-based scrape (Cloudflare-friendly) if available.
        # Set CDP_URL env var for dashboard runs.
        cdp_url = os.getenv("CDP_URL", "http://172.21.160.1:9330").strip() or None
        url = args.tanitjobs_url or "https://www.tanitjobs.com/jobs/"

        page_jobs, reason = fetch_first_page_jobs(
            url,
            user_data_dir=None,
            headless=True,
            timeout_ms=30_000,
            cdp_url=cdp_url,
            max_jobs=80,
        )
        if reason.startswith("cdp_error"):
            print(f"tanitjobs: {reason}")
            return 2

        jobs = []
        from .models import Job

        card_text_by_url = {}
        for item in page_jobs:
            jid = str(item.get("id") or "").strip()
            title = (item.get("title") or "").strip()
            company = (item.get("company") or "").strip()
            location = (item.get("location") or "").strip()
            job_url = (item.get("url") or f"https://www.tanitjobs.com/job/{jid}/").strip()
            card_text = (item.get("card_text") or "").strip()
            jobs.append(
                Job(
                    source="tanitjobs",
                    external_id=jid,
                    title=title,
                    company=company,
                    location=location,
                    url=job_url,
                    posted_at=None,
                )
            )
            if card_text:
                card_text_by_url[job_url] = card_text

        new_jobs = db.upsert_jobs(jobs)
        if new_jobs and card_text_by_url:
            cache_db = JobTextCacheDB(Path("data") / "jobs.sqlite3")
            try:
                for j in new_jobs:
                    txt = card_text_by_url.get(j.url)
                    if not txt:
                        continue
                    cache_db.upsert(url_canon=canonicalize_url(j.url), url=j.url, text=txt, method="tanitjobs_card", status="ok", error=None)
            finally:
                cache_db.close()
        relevant_new = [j for j in new_jobs if is_relevant(j.title)]

        print(f"tanitjobs: scraped={len(jobs)} new={len(new_jobs)} relevant_new={len(relevant_new)}")
        for j in new_jobs[:40]:
            print(f"TANIT_NEW: {j.title} | {j.company} | {j.location} | {j.url}")
        for j in relevant_new[:20]:
            print(f"NEW: {j.title} | {j.url}")

        if args.sheet_id:
            append_jobs_routed(args.sheet_id, args.sheet_account, relevant_new, today_label, "Sales_Today", "Tech_Today")

        if args.notify and relevant_new:
            lines = [f"{j.title}\n{j.url}" for j in relevant_new]
            send_summary(title=f"tanitjobs: {len(relevant_new)} new relevant", lines=lines)

    if args.source == "keejob":
        cfg = KeejobConfig(today_only=True)
        jobs, _date_label = scrape_keejob(cfg=cfg)
        new_jobs = db.upsert_jobs(jobs)

        relevant_new = [j for j in new_jobs if is_relevant(j.title)]

        print(f"keejob: scraped={len(jobs)} new={len(new_jobs)} relevant_new={len(relevant_new)}")
        for j in relevant_new[:20]:
            print(f"NEW: {j.title} | {j.company} | {j.location} | {j.url}")

        if args.sheet_id:
            append_jobs_routed(args.sheet_id, args.sheet_account, relevant_new, today_label, "Sales_Today", "Tech_Today")

        if args.notify and relevant_new:
            lines = [f"{j.title} | {j.url}" for j in relevant_new]
            send_summary(title=f"Keejob: {len(relevant_new)} new relevant", lines=lines)

    if args.source == "welcometothejungle":
        cfg = WTTJConfig(days=1, max_detail_pages=40, max_per_company=5)
        jobs, _date_label = scrape_wttj(cfg=cfg)
        new_jobs = db.upsert_jobs(jobs)

        relevant_new = [j for j in new_jobs if is_relevant(j.title)]

        print(f"welcometothejungle: scraped={len(jobs)} new={len(new_jobs)} relevant_new={len(relevant_new)}")
        for j in relevant_new[:20]:
            print(f"NEW: {j.title} | {j.company} | {j.url}")

        if args.sheet_id:
            append_jobs_routed(args.sheet_id, args.sheet_account, relevant_new, today_label, "Sales_Today", "Tech_Today")

        if args.notify and relevant_new:
            lines = [f"{j.title} | {j.url}" for j in relevant_new]
            send_summary(title=f"welcometothejungle: {len(relevant_new)} new relevant", lines=lines)

    if args.source == "weworkremotely":
        cfg = WWRConfig()
        jobs, _date_label = scrape_weworkremotely(cfg=cfg)
        new_jobs = db.upsert_jobs(jobs)

        relevant_new = [j for j in new_jobs if is_relevant(j.title)]

        print(f"weworkremotely: scraped={len(jobs)} new={len(new_jobs)} relevant_new={len(relevant_new)}")
        for j in relevant_new[:20]:
            print(f"NEW: {j.title} | {j.company} | {j.url}")

        if args.sheet_id:
            append_jobs_routed(args.sheet_id, args.sheet_account, relevant_new, today_label, "Sales_Today", "Tech_Today")

        if args.notify and relevant_new:
            lines = [f"{j.title} | {j.url}" for j in relevant_new]
            send_summary(title=f"weworkremotely: {len(relevant_new)} new relevant", lines=lines)

    if args.source == "remoteok":
        cfg = RemoteOKConfig()
        jobs, _date_label = scrape_remoteok(cfg=cfg)
        new_jobs = db.upsert_jobs(jobs)

        relevant_new = [j for j in new_jobs if is_relevant(j.title)]

        print(f"remoteok: scraped={len(jobs)} new={len(new_jobs)} relevant_new={len(relevant_new)}")
        for j in relevant_new[:20]:
            print(f"NEW: {j.title} | {j.company} | {j.url}")

        if args.sheet_id:
            append_jobs_routed(args.sheet_id, args.sheet_account, relevant_new, today_label, "Sales_Today", "Tech_Today")

        if args.notify and relevant_new:
            lines = [f"{j.title} | {j.url}" for j in relevant_new]
            send_summary(title=f"remoteok: {len(relevant_new)} new relevant", lines=lines)

    if args.source == "remotive":
        cfg = RemotiveConfig()
        jobs, _date_label = scrape_remotive(cfg=cfg)
        new_jobs = db.upsert_jobs(jobs)

        relevant_new = [j for j in new_jobs if is_relevant(j.title)]

        print(f"remotive: scraped={len(jobs)} new={len(new_jobs)} relevant_new={len(relevant_new)}")
        for j in relevant_new[:20]:
            print(f"NEW: {j.title} | {j.company} | {j.url}")

        if args.sheet_id:
            append_jobs_routed(args.sheet_id, args.sheet_account, relevant_new, today_label, "Sales_Today", "Tech_Today")

        if args.notify and relevant_new:
            lines = [f"{j.title} | {j.url}" for j in relevant_new]
            send_summary(title=f"remotive: {len(relevant_new)} new relevant", lines=lines)

    if args.source == "linkedin":
        # CDP-only: use your logged-in Windows Chrome.
        cdp_url = os.getenv("CDP_URL", "http://172.21.160.1:9330").strip() or "http://172.21.160.1:9330"
        # Allow multiple LinkedIn searches:
        # - CLI: --linkedin-url runs a single URL
        # - env: LINKEDIN_URLS can contain newline-separated or comma-separated URLs
        #   (falls back to LINKEDIN_URL if LINKEDIN_URLS is empty)
        raw_urls = (os.getenv("LINKEDIN_URLS") or "").strip()
        urls: list[str] = []
        if args.linkedin_url:
            urls = [args.linkedin_url]
        elif raw_urls:
            # split by newline or comma
            parts = []
            for line in raw_urls.splitlines():
                parts.extend([p.strip() for p in line.split(",") if p.strip()])
            urls = [p for p in parts if p]
        else:
            urls = [
                (os.getenv("LINKEDIN_URL") or "").strip()
                or "https://www.linkedin.com/jobs/search/?geoId=102134353&f_TPR=r7200&sortBy=DD"
            ]

        def _label_for_url(u: str) -> str:
            # Best-effort labels for the known geoIds/locations we use.
            ul = u.lower()
            if "geoid=102134353" in ul:
                return "TN"
            if "geoid=105015875" in ul:
                return "FR"
            if "geoid=101282230" in ul:
                # Dashboard uses GR label for Germany.
                return "GR"
            if "location=middle%20east" in ul or "region=me" in ul:
                return "ME"
            return "LI"

        all_jobs = []
        scraped_total = 0
        by_label_scraped: dict[str, int] = {}
        id_to_label: dict[str, str] = {}

        for url in urls:
            label = _label_for_url(url)
            cfg = LinkedInCDPConfig(cdp_url=cdp_url, url=url, max_jobs=80)
            jobs, reason = scrape_linkedin_first_page(cfg=cfg)
            if reason.startswith("cdp_error"):
                print(f"linkedin: {reason}")
                return 2

            # New rule: for Germany (GR), drop jobs whose title is not English (heuristic).
            if label in {"GR", "ME"}:
                jobs = [j for j in jobs if is_english_title(j.title)]

            # Store per-geo source in SQLite (e.g. 'linkedin TN') so history matches Sheets.
            jobs = [replace(j, source=f"linkedin {label}") for j in jobs]
            scraped_total += len(jobs)
            by_label_scraped[label] = by_label_scraped.get(label, 0) + len(jobs)
            for j in jobs:
                id_to_label[j.external_id] = label
            all_jobs.extend(jobs)

        new_jobs = db.upsert_jobs(all_jobs)
        relevant_new = [j for j in new_jobs if is_relevant(j.title)]

        # Make the first line match the dashboard STAT_RE (source: scraped=.. new=.. relevant_new=..)
        # and keep extra details after.
        by_label_new: dict[str, int] = {}
        by_label_rel: dict[str, int] = {}
        for j in new_jobs:
            lab = id_to_label.get(j.external_id, "LI")
            by_label_new[lab] = by_label_new.get(lab, 0) + 1
        for j in relevant_new:
            lab = id_to_label.get(j.external_id, "LI")
            by_label_rel[lab] = by_label_rel.get(lab, 0) + 1

        details = " | ".join(
            f"{lab}: scraped={by_label_scraped.get(lab, 0)} new={by_label_new.get(lab, 0)} rel={by_label_rel.get(lab, 0)}"
            for lab in ["TN", "FR", "GR", "LI"]
            if (by_label_scraped.get(lab, 0) or by_label_new.get(lab, 0) or by_label_rel.get(lab, 0))
        )

        print(
            f"linkedin: scraped={scraped_total} new={len(new_jobs)} relevant_new={len(relevant_new)}"
            + (f" | {details}" if details else "")
        )

        for j in relevant_new[:20]:
            print(f"NEW: {j.title} | {j.company} | {j.location} | {j.url}")

        if args.sheet_id:
            # Route LinkedIn jobs the same way as Keejob/Tanitjobs.
            append_jobs_routed(args.sheet_id, args.sheet_account, relevant_new, today_label, "Sales_Today", "Tech_Today")

        if args.notify and relevant_new:
            lines = [f"{j.title} | {j.url}" for j in relevant_new]
            send_summary(title=f"LinkedIn: {len(relevant_new)} new relevant", lines=lines)

    db.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
