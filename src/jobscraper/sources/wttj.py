from __future__ import annotations

import datetime as dt
import gzip
import io
import re
import xml.etree.ElementTree as ET
from dataclasses import dataclass
from typing import Dict, Iterable, List, Optional, Tuple

import requests
from selectolax.parser import HTMLParser

from ..models import Job


INDEX_URL = "https://www.welcometothejungle.com/sitemaps/index.xml.gz"

_NS = {"sm": "http://www.sitemaps.org/schemas/sitemap/0.9"}


@dataclass
class WTTJConfig:
    # How far back to consider links as "new enough".
    # Normal mode: keep this small because we run often.
    days: int = 1

    # Limit per run so we don't hammer the site with detail page fetches.
    max_detail_pages: int = 40

    # Avoid a single employer flooding the run.
    max_per_company: int = 5

    # Prefer languages when selecting candidates.
    prefer_langs: tuple[str, ...] = ("en", "fr")

    timeout_s: int = 30

    user_agent: str = "Mozilla/5.0 (compatible; job-scraper/0.1)"


def _utcnow() -> dt.datetime:
    return dt.datetime.now(dt.timezone.utc)


def _cutoff(cfg: WTTJConfig) -> dt.datetime:
    return _utcnow() - dt.timedelta(days=cfg.days)


def _get(url: str, cfg: WTTJConfig) -> requests.Response:
    return requests.get(url, headers={"User-Agent": cfg.user_agent}, timeout=cfg.timeout_s)


def _read_xml_maybe_gzip(content: bytes) -> bytes:
    if content[:2] == b"\x1f\x8b":
        return gzip.decompress(content)
    return content


def _parse_index(cfg: WTTJConfig) -> List[str]:
    resp = _get(INDEX_URL, cfg)
    resp.raise_for_status()
    root = ET.fromstring(_read_xml_maybe_gzip(resp.content))

    locs: List[str] = []
    for sm in root.findall("sm:sitemap", _NS):
        loc_el = sm.find("sm:loc", _NS)
        if loc_el is not None and loc_el.text:
            locs.append(loc_el.text.strip())

    # Prefer job listing sitemaps.
    job_sitemaps = [u for u in locs if "/sitemaps/job-listings." in u]

    # These are numbered 0..N but not necessarily sorted by recency.
    # Still, we keep in that order and rely on lastmod filtering.
    return job_sitemaps


def _iter_job_urls_from_sitemap(sitemap_url: str, cfg: WTTJConfig) -> Iterable[Tuple[str, Optional[dt.datetime]]]:
    resp = _get(sitemap_url, cfg)
    resp.raise_for_status()
    xml_bytes = _read_xml_maybe_gzip(resp.content)
    root = ET.fromstring(xml_bytes)

    for url_el in root.findall("sm:url", _NS):
        loc_el = url_el.find("sm:loc", _NS)
        if loc_el is None or not loc_el.text:
            continue
        loc = loc_el.text.strip()

        last_el = url_el.find("sm:lastmod", _NS)
        lastmod = None
        if last_el is not None and last_el.text:
            try:
                # e.g. 2026-01-31T01:01:17+00:00
                lastmod = dt.datetime.fromisoformat(last_el.text.replace("Z", "+00:00"))
                if lastmod.tzinfo is None:
                    lastmod = lastmod.replace(tzinfo=dt.timezone.utc)
            except Exception:
                lastmod = None

        yield loc, lastmod


def _extract_title_from_job_page(html: str) -> str:
    p = HTMLParser(html)
    h1 = p.css_first("h1")
    if h1:
        t = h1.text(strip=True)
        if t:
            return t
    title = p.css_first("title")
    if title:
        t = title.text(strip=True)
        if t:
            # Often like: "Senior Data Engineering Manager - TheFork - Welcome to the Jungle"
            return t.split(" - Welcome to the Jungle")[0].strip()
    return ""


def _guess_company_from_url(url: str) -> str:
    # Common pattern: /fr/companies/<company>/jobs/<slug>...
    m = re.search(r"/companies/([^/]+)/jobs/", url)
    if m:
        return m.group(1).replace("-", " ")
    return ""


def scrape_wttj(cfg: Optional[WTTJConfig] = None) -> tuple[List[Job], str]:
    cfg = cfg or WTTJConfig()
    cutoff = _cutoff(cfg)

    # 1) Gather candidate job URLs from sitemaps (lastmod >= cutoff)
    candidates: List[Tuple[str, dt.datetime]] = []
    for sm_url in _parse_index(cfg):
        for loc, lastmod in _iter_job_urls_from_sitemap(sm_url, cfg):
            if not lastmod:
                continue
            if lastmod >= cutoff:
                candidates.append((loc, lastmod))

    # newest first
    candidates.sort(key=lambda x: x[1], reverse=True)

    def lang_rank(url: str) -> int:
        # Prefer /en/ over /fr/ (or custom order).
        for i, lang in enumerate(cfg.prefer_langs):
            if f"/{lang}/" in url:
                return i
        return len(cfg.prefer_langs)

    # 2) Select candidates with per-company cap + language preference.
    selected: List[Tuple[str, dt.datetime]] = []
    per_company: Dict[str, int] = {}

    # iterate by (lang preference, recency) while preserving recency within each language
    for loc, lastmod in sorted(candidates, key=lambda x: (lang_rank(x[0]), -x[1].timestamp())):
        if len(selected) >= cfg.max_detail_pages:
            break
        company = _guess_company_from_url(loc) or "(unknown)"
        if per_company.get(company, 0) >= cfg.max_per_company:
            continue
        per_company[company] = per_company.get(company, 0) + 1
        selected.append((loc, lastmod))

    # 3) Fetch detail pages to extract titles
    jobs: List[Job] = []
    for loc, lastmod in selected:
        try:
            resp = _get(loc, cfg)
            if resp.status_code != 200:
                continue
            title = _extract_title_from_job_page(resp.text) or "(unknown)"
            company = _guess_company_from_url(loc)
            jobs.append(
                Job(
                    source="welcometothejungle",
                    external_id=loc,  # stable enough
                    title=title,
                    company=company,
                    location="",
                    url=loc,
                    posted_at=lastmod,
                )
            )
        except Exception:
            continue

    date_label = f"lastmod>= {cutoff.date().isoformat()} (UTC)"
    return jobs, date_label
