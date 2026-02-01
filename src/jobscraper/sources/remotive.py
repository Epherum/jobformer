from __future__ import annotations

import datetime as dt
from dataclasses import dataclass
from typing import List, Optional, Tuple

import requests

from ..models import Job


API_URL = "https://remotive.com/api/remote-jobs"


@dataclass
class RemotiveConfig:
    api_url: str = API_URL
    timeout_s: int = 30
    user_agent: str = "Mozilla/5.0 (compatible; job-scraper/0.1)"


def _parse_iso(s: str) -> Optional[dt.datetime]:
    if not s:
        return None
    try:
        # Example: 2026-01-30T12:34:56
        d = dt.datetime.fromisoformat(s.replace("Z", "+00:00"))
        if d.tzinfo is None:
            d = d.replace(tzinfo=dt.timezone.utc)
        return d.astimezone(dt.timezone.utc)
    except Exception:
        return None


def scrape_remotive(cfg: Optional[RemotiveConfig] = None) -> Tuple[List[Job], str]:
    """Scrape Remotive via its public API.

    Note: Remotive states their public API results can be delayed by ~24h.
    """
    cfg = cfg or RemotiveConfig()

    resp = requests.get(cfg.api_url, headers={"User-Agent": cfg.user_agent}, timeout=cfg.timeout_s)
    resp.raise_for_status()
    data = resp.json()

    jobs: List[Job] = []
    for j in data.get("jobs", []) or []:
        jid = str(j.get("id") or "")
        url = (j.get("url") or "").strip()
        title = (j.get("title") or "").strip()
        company = (j.get("company_name") or "").strip()
        location = (j.get("candidate_required_location") or "remote").strip() or "remote"
        pub = _parse_iso((j.get("publication_date") or "").strip())

        if not jid:
            # fall back to url
            jid = url or title
        if not url:
            continue

        jobs.append(
            Job(
                source="remotive",
                external_id=jid,
                title=title or "(unknown)",
                company=company,
                location=location,
                url=url,
                posted_at=pub,
            )
        )

    return jobs, "api (may be delayed ~24h)"
