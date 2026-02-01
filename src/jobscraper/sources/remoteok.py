from __future__ import annotations

import datetime as dt
import xml.etree.ElementTree as ET
from dataclasses import dataclass
from typing import List, Optional, Tuple

import requests

from ..models import Job


RSS_URL = "https://remoteok.com/remote-jobs.rss"


@dataclass
class RemoteOKConfig:
    rss_url: str = RSS_URL
    timeout_s: int = 30
    user_agent: str = "Mozilla/5.0 (compatible; job-scraper/0.1)"


def _parse_rfc2822_date(s: str) -> Optional[dt.datetime]:
    try:
        from email.utils import parsedate_to_datetime

        d = parsedate_to_datetime(s)
        if d.tzinfo is None:
            d = d.replace(tzinfo=dt.timezone.utc)
        return d.astimezone(dt.timezone.utc)
    except Exception:
        return None


def scrape_remoteok(cfg: Optional[RemoteOKConfig] = None) -> Tuple[List[Job], str]:
    cfg = cfg or RemoteOKConfig()

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
        company = (item.findtext("company") or "").strip()
        pub_date = (item.findtext("pubDate") or "").strip()
        published_at = _parse_rfc2822_date(pub_date) if pub_date else None

        if not link:
            continue

        jobs.append(
            Job(
                source="remoteok",
                external_id=link,
                title=title or "(unknown)",
                company=company,
                location="remote",
                url=link,
                posted_at=published_at,
            )
        )

    return jobs, "rss"
