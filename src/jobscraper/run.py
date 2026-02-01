from __future__ import annotations

import argparse
from pathlib import Path

from .db import JobDB
from .sources.tanitjobs import TanitjobsConfig, scrape_tanitjobs
from .sources.keejob import KeejobConfig, scrape_keejob
from .sources.wttj import WTTJConfig, scrape_wttj
from .sources.weworkremotely import WWRConfig, scrape_weworkremotely
from .sources.remoteok import RemoteOKConfig, scrape_remoteok
from .sources.remotive import RemotiveConfig, scrape_remotive
from .sources.aneti import AnetiConfig, scrape_aneti
from .filtering import is_relevant
from .sheets_sync import SheetsConfig, append_jobs, ensure_jobs_header


def main() -> int:
    p = argparse.ArgumentParser()
    p.add_argument(
        "--source",
        choices=["tanitjobs", "keejob", "welcometothejungle", "weworkremotely", "remoteok", "remotive", "aneti"],
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
    p.add_argument("--sheet-tab", default="Jobs", help="Sheet tab name")
    p.add_argument("--sheet-account", default="wassimfekih2@gmail.com", help="gog account email")
    args = p.parse_args()

    db = JobDB(Path("data") / "jobs.sqlite3")

    if args.source == "tanitjobs":
        cfg = TanitjobsConfig()
        if args.tanitjobs_url:
            cfg.search_url = args.tanitjobs_url
        if args.browser_channel:
            cfg.browser_channel = args.browser_channel
        if args.user_data_dir:
            cfg.user_data_dir = args.user_data_dir

        jobs = scrape_tanitjobs(cfg=cfg, headed=args.headed)
        new_jobs = db.upsert_jobs(jobs)

        print(f"tanitjobs: scraped={len(jobs)} new={len(new_jobs)}")
        for j in new_jobs[:20]:
            print(f"NEW: {j.title} {j.url}")

    if args.source == "keejob":
        cfg = KeejobConfig(today_only=True)
        jobs, date_label = scrape_keejob(cfg=cfg)
        new_jobs = db.upsert_jobs(jobs)

        relevant_new = [j for j in new_jobs if is_relevant(j.title)]

        print(f"keejob: scraped={len(jobs)} new={len(new_jobs)} relevant_new={len(relevant_new)}")
        for j in relevant_new[:20]:
            print(f"NEW: {j.title} | {j.company} | {j.location} | {j.url}")

        if args.sheet_id:
            scfg = SheetsConfig(sheet_id=args.sheet_id, tab=args.sheet_tab, account=args.sheet_account)
            ensure_jobs_header(scfg)
            append_jobs(scfg, relevant_new, date_label=date_label)

    if args.source == "welcometothejungle":
        cfg = WTTJConfig(days=1, max_detail_pages=40, max_per_company=5)
        jobs, date_label = scrape_wttj(cfg=cfg)
        new_jobs = db.upsert_jobs(jobs)

        relevant_new = [j for j in new_jobs if is_relevant(j.title)]

        print(f"welcometothejungle: scraped={len(jobs)} new={len(new_jobs)} relevant_new={len(relevant_new)}")
        for j in relevant_new[:20]:
            print(f"NEW: {j.title} | {j.company} | {j.url}")

        if args.sheet_id:
            scfg = SheetsConfig(sheet_id=args.sheet_id, tab=args.sheet_tab, account=args.sheet_account)
            ensure_jobs_header(scfg)
            append_jobs(scfg, relevant_new, date_label=date_label)

    if args.source == "weworkremotely":
        cfg = WWRConfig()
        jobs, date_label = scrape_weworkremotely(cfg=cfg)
        new_jobs = db.upsert_jobs(jobs)

        relevant_new = [j for j in new_jobs if is_relevant(j.title)]

        print(f"weworkremotely: scraped={len(jobs)} new={len(new_jobs)} relevant_new={len(relevant_new)}")
        for j in relevant_new[:20]:
            print(f"NEW: {j.title} | {j.company} | {j.url}")

        if args.sheet_id:
            scfg = SheetsConfig(sheet_id=args.sheet_id, tab=args.sheet_tab, account=args.sheet_account)
            ensure_jobs_header(scfg)
            append_jobs(scfg, relevant_new, date_label=date_label)

    if args.source == "remoteok":
        cfg = RemoteOKConfig()
        jobs, date_label = scrape_remoteok(cfg=cfg)
        new_jobs = db.upsert_jobs(jobs)

        relevant_new = [j for j in new_jobs if is_relevant(j.title)]

        print(f"remoteok: scraped={len(jobs)} new={len(new_jobs)} relevant_new={len(relevant_new)}")
        for j in relevant_new[:20]:
            print(f"NEW: {j.title} | {j.company} | {j.url}")

        if args.sheet_id:
            scfg = SheetsConfig(sheet_id=args.sheet_id, tab=args.sheet_tab, account=args.sheet_account)
            ensure_jobs_header(scfg)
            append_jobs(scfg, relevant_new, date_label=date_label)

    if args.source == "remotive":
        cfg = RemotiveConfig()
        jobs, date_label = scrape_remotive(cfg=cfg)
        new_jobs = db.upsert_jobs(jobs)

        relevant_new = [j for j in new_jobs if is_relevant(j.title)]

        print(f"remotive: scraped={len(jobs)} new={len(new_jobs)} relevant_new={len(relevant_new)}")
        for j in relevant_new[:20]:
            print(f"NEW: {j.title} | {j.company} | {j.url}")

        if args.sheet_id:
            scfg = SheetsConfig(sheet_id=args.sheet_id, tab=args.sheet_tab, account=args.sheet_account)
            ensure_jobs_header(scfg)
            append_jobs(scfg, relevant_new, date_label=date_label)

    if args.source == "aneti":
        # CDP-only: ANETI blocks our server IP, so we use your Windows Chrome session.
        cfg = AnetiConfig(cdp_url="http://172.25.192.1:9223", max_offers=25)
        jobs, date_label = scrape_aneti(cfg=cfg)
        new_jobs = db.upsert_jobs(jobs)

        relevant_new = [j for j in new_jobs if is_relevant(j.title)]

        print(f"aneti: scraped={len(jobs)} new={len(new_jobs)} relevant_new={len(relevant_new)}")
        for j in relevant_new[:20]:
            print(f"NEW: {j.title} | {j.url}")

        if args.sheet_id:
            scfg = SheetsConfig(sheet_id=args.sheet_id, tab=args.sheet_tab, account=args.sheet_account)
            ensure_jobs_header(scfg)
            append_jobs(scfg, relevant_new, date_label=date_label)

    db.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
