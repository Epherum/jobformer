from __future__ import annotations

import datetime as dt
import re
from dataclasses import dataclass
from typing import Iterable, List, Optional
from urllib.parse import urljoin

import requests
from selectolax.parser import HTMLParser

from ..models import Job


BASE = "https://www.keejob.com"

_JOB_ID_RE = re.compile(r"^/offres-emploi/(\d+)/")
_DATE_RE = re.compile(r"\b(\d{1,2})\s+([a-zéûôîàç]+)\s+(\d{4})\b", re.IGNORECASE)

_MONTHS_FR = {
    1: "janvier",
    2: "février",
    3: "mars",
    4: "avril",
    5: "mai",
    6: "juin",
    7: "juillet",
    8: "août",
    9: "septembre",
    10: "octobre",
    11: "novembre",
    12: "décembre",
}


@dataclass
class KeejobConfig:
    # Main listing.
    list_url_template: str = BASE + "/offres-emploi/?search=1&page={page}"

    # Safety limits.
    max_pages: int = 10
    timeout_s: int = 30

    # Only ingest jobs that show the given Keejob date label.
    # If None, ingest everything from scanned pages.
    today_only: bool = True

    user_agent: str = "Mozilla/5.0 (compatible; job-scraper/0.1; +https://wassimfekih.com)"


def _today_fr(tz_offset_hours: int = 1) -> str:
    tz = dt.timezone(dt.timedelta(hours=tz_offset_hours))
    now = dt.datetime.now(dt.timezone.utc).astimezone(tz)
    return f"{now.day} {_MONTHS_FR[now.month]} {now.year}"


def _parse_list_page(html: str) -> List[dict]:
    p = HTMLParser(html)
    jobs: List[dict] = []

    for art in p.css("article"):
        title_a = art.css_first("h2 a")
        if not title_a:
            continue

        href = title_a.attributes.get("href", "")
        m = _JOB_ID_RE.match(href)
        if not m:
            continue
        job_id = m.group(1)

        title = title_a.text(strip=True)
        url = urljoin(BASE, href.split("?")[0])

        company = ""
        for a in art.css("a"):
            h = a.attributes.get("href", "")
            if h.startswith("/offres-emploi/companies/"):
                t = a.text(strip=True)
                if t:
                    company = t
                    break

        lines = [l.strip() for l in art.text(separator="\n", strip=True).split("\n") if l.strip()]

        date_line = ""
        for l in lines:
            if _DATE_RE.search(l):
                date_line = l

        location = ""
        if date_line and date_line in lines:
            idx = lines.index(date_line)
            if idx > 0:
                location = lines[idx - 1].strip()

        jobs.append(
            {
                "id": job_id,
                "title": title,
                "company": company,
                "location": location,
                "date": date_line,
                "url": url,
            }
        )

    return jobs


def scrape_keejob(cfg: Optional[KeejobConfig] = None) -> tuple[List[Job], str]:
    cfg = cfg or KeejobConfig()

    headers = {"User-Agent": cfg.user_agent}
    today_fr = _today_fr()

    out: List[Job] = []

    for page in range(1, cfg.max_pages + 1):
        url = cfg.list_url_template.format(page=page)
        resp = requests.get(url, headers=headers, timeout=cfg.timeout_s)
        resp.raise_for_status()

        page_jobs = _parse_list_page(resp.text)
        if not page_jobs:
            break

        # Stop condition: if today_only and this page has no jobs stamped today.
        if cfg.today_only:
            any_today = any(j.get("date") == today_fr for j in page_jobs)
            if not any_today:
                break

        for j in page_jobs:
            if cfg.today_only and j.get("date") != today_fr:
                continue

            out.append(
                Job(
                    source="keejob",
                    external_id=j["id"],
                    title=j.get("title") or "(unknown)",
                    company=j.get("company") or "",
                    location=j.get("location") or "",
                    url=j.get("url") or "",
                    posted_at=None,
                )
            )

    return out, today_fr
