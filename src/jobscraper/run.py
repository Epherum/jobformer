from __future__ import annotations

import argparse
import datetime as dt
import os
from pathlib import Path

from .db import JobDB

from .sources.tanitjobs import TanitjobsConfig, scrape_tanitjobs
from .tanitjobs_watch import fetch_first_page_jobs
from .sources.keejob import KeejobConfig, scrape_keejob
from .sources.wttj import WTTJConfig, scrape_wttj
from .sources.weworkremotely import WWRConfig, scrape_weworkremotely
from .sources.remoteok import RemoteOKConfig, scrape_remoteok
from .sources.remotive import RemotiveConfig, scrape_remotive
from .sources.aneti import AnetiConfig, scrape_aneti
from .sources.linkedin_cdp import LinkedInCDPConfig, scrape_linkedin_first_page
from .filtering import is_relevant
from .sheets_sync import SheetsConfig, append_jobs, ensure_jobs_header
from .alerts.pushover import send_summary


def main() -> int:
    p = argparse.ArgumentParser()
    p.add_argument(
        "--source",
        choices=["tanitjobs", "keejob", "welcometothejungle", "weworkremotely", "remoteok", "remotive", "aneti", "linkedin"],
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
        cdp_url = os.getenv("CDP_URL", "http://172.25.192.1:9223").strip() or None
        url = args.tanitjobs_url or "https://www.tanitjobs.com/jobs/"

        page_jobs, reason = fetch_first_page_jobs(
            url,
            user_data_dir=None,
            headless=True,
            timeout_ms=30_000,
            cdp_url=cdp_url,
            max_jobs=80,
        )

        jobs = []
        from .models import Job

        for jid, title in page_jobs:
            jobs.append(
                Job(
                    source="tanitjobs",
                    external_id=str(jid),
                    title=title,
                    company="",
                    location="",
                    url=f"https://www.tanitjobs.com/job/{jid}/",
                    posted_at=None,
                )
            )

        new_jobs = db.upsert_jobs(jobs)
        relevant_new = [j for j in new_jobs if is_relevant(j.title)]

        print(f"tanitjobs: scraped={len(jobs)} new={len(new_jobs)} relevant_new={len(relevant_new)}")
        for j in relevant_new[:20]:
            print(f"NEW: {j.title} | {j.url}")

        if args.sheet_id:
            scfg = SheetsConfig(sheet_id=args.sheet_id, tab=args.sheet_tab, account=args.sheet_account)
            ensure_jobs_header(scfg)
            append_jobs(scfg, relevant_new, date_label=today_label)

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
            scfg = SheetsConfig(sheet_id=args.sheet_id, tab=args.sheet_tab, account=args.sheet_account)
            ensure_jobs_header(scfg)
            append_jobs(scfg, relevant_new, date_label=today_label)

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
            scfg = SheetsConfig(sheet_id=args.sheet_id, tab=args.sheet_tab, account=args.sheet_account)
            ensure_jobs_header(scfg)
            append_jobs(scfg, relevant_new, date_label=today_label)

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
            scfg = SheetsConfig(sheet_id=args.sheet_id, tab=args.sheet_tab, account=args.sheet_account)
            ensure_jobs_header(scfg)
            append_jobs(scfg, relevant_new, date_label=today_label)

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
            scfg = SheetsConfig(sheet_id=args.sheet_id, tab=args.sheet_tab, account=args.sheet_account)
            ensure_jobs_header(scfg)
            append_jobs(scfg, relevant_new, date_label=today_label)

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
            scfg = SheetsConfig(sheet_id=args.sheet_id, tab=args.sheet_tab, account=args.sheet_account)
            ensure_jobs_header(scfg)
            append_jobs(scfg, relevant_new, date_label=today_label)

        if args.notify and relevant_new:
            lines = [f"{j.title} | {j.url}" for j in relevant_new]
            send_summary(title=f"remotive: {len(relevant_new)} new relevant", lines=lines)

    if args.source == "aneti":
        # CDP-only: ANETI blocks our server IP, so we use your Windows Chrome session.
        cdp_url = os.getenv("CDP_URL", "http://172.25.192.1:9223").strip() or "http://172.25.192.1:9223"
        cfg = AnetiConfig(cdp_url=cdp_url, max_offers=25)
        jobs, _date_label = scrape_aneti(cfg=cfg)
        new_jobs = db.upsert_jobs(jobs)

        relevant_new = [j for j in new_jobs if is_relevant(j.title)]

        print(f"aneti: scraped={len(jobs)} new={len(new_jobs)} relevant_new={len(relevant_new)}")
        for j in relevant_new[:20]:
            print(f"NEW: {j.title} | {j.url}")

        if args.sheet_id:
            scfg = SheetsConfig(sheet_id=args.sheet_id, tab=args.sheet_tab, account=args.sheet_account)
            ensure_jobs_header(scfg)
            append_jobs(scfg, relevant_new, date_label=today_label)

    if args.source == "linkedin":
        # CDP-only: use your logged-in Windows Chrome.
        cdp_url = os.getenv("CDP_URL", "http://172.25.192.1:9223").strip() or "http://172.25.192.1:9223"
        url = (
            args.linkedin_url
            or (os.getenv("LINKEDIN_URL") or "").strip()
            or "https://www.linkedin.com/jobs/search/?geoId=102134353&f_TPR=r7200&sortBy=DD"
        )

        cfg = LinkedInCDPConfig(cdp_url=cdp_url, url=url, max_jobs=80)
        jobs, _reason = scrape_linkedin_first_page(cfg=cfg)
        new_jobs = db.upsert_jobs(jobs)

        relevant_new = [j for j in new_jobs if is_relevant(j.title)]

        print(f"linkedin: scraped={len(jobs)} new={len(new_jobs)} relevant_new={len(relevant_new)}")
        for j in relevant_new[:20]:
            print(f"NEW: {j.title} | {j.company} | {j.location} | {j.url}")

        if args.sheet_id:
            scfg = SheetsConfig(sheet_id=args.sheet_id, tab=args.sheet_tab, account=args.sheet_account)
            ensure_jobs_header(scfg)
            append_jobs(scfg, relevant_new, date_label=today_label)

        if args.notify and relevant_new:
            lines = [f"{j.title} | {j.url}" for j in relevant_new]
            send_summary(title=f"LinkedIn: {len(relevant_new)} new relevant", lines=lines)

    db.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
