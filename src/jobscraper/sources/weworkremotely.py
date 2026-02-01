from __future__ import annotations

import datetime as dt
import re
import xml.etree.ElementTree as ET
from dataclasses import dataclass
from typing import List, Optional, Tuple

import requests

from ..models import Job


RSS_URL = "https://weworkremotely.com/categories/remote-programming-jobs"


@dataclass
class WWRConfig:
    # We Work Remotely provides RSS for categories.
    rss_url: str = RSS_URL
    timeout_s: int = 30
    user_agent: str = "Mozilla/5.0 (compatible; job-scraper/0.1)"


def _parse_rfc2822_date(s: str) -> Optional[dt.datetime]:
    # RSS pubDate is RFC2822. Example: "Fri, 31 Jan 2026 19:42:10 +0000"
    try:
        from email.utils import parsedate_to_datetime

        d = parsedate_to_datetime(s)
        if d.tzinfo is None:
            d = d.replace(tzinfo=dt.timezone.utc)
        return d.astimezone(dt.timezone.utc)
    except Exception:
        return None


def scrape_weworkremotely(cfg: Optional[WWRConfig] = None) -> Tuple[List[Job], str]:
    cfg = cfg or WWRConfig()

    resp = requests.get(cfg.rss_url, headers={"User-Agent": cfg.user_agent}, timeout=cfg.timeout_s)
    resp.raise_for_status()

    root = ET.fromstring(resp.content)
    channel = root.find("channel")
    if channel is None:
        return [], "rss"

    jobs: List[Job] = []

    for item in channel.findall("item"):
        title = (item.findtext("title") or "").strip()
        link = (item.findtext("link") or "").strip()
        pub_date = (item.findtext("pubDate") or "").strip()
        published_at = _parse_rfc2822_date(pub_date)

        # Company is usually before colon in the RSS title: "Company: Role"
        company = ""
        role = title
        if ":" in title:
            company, role = [x.strip() for x in title.split(":", 1)]

        # Category appears as <category> in RSS but not always useful. Location not provided.

        if not link:
            continue

        external_id = link
        jobs.append(
            Job(
                source="weworkremotely",
                external_id=external_id,
                title=role or title or "(unknown)",
                company=company,
                location="remote",
                url=link,
                posted_at=published_at,
            )
        )

    return jobs, "rss"
