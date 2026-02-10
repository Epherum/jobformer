from __future__ import annotations

import os
import re
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass
from typing import Iterable, Optional
from urllib.parse import urlparse

import requests

try:
    from selectolax.parser import HTMLParser
except Exception:  # pragma: no cover
    HTMLParser = None

from .cdp_page_fetch import fetch_page_text_via_cdp
from .page_fetch import DEFAULT_UA
from .sheets_sync import SheetsConfig, _get_sheet_rows
from .tanitjobs_page_fetch import fetch_tanitjobs_page_text
from .job_text_cache_db import JobTextCacheDB
from .url_canon import canonicalize_url


CF_HEAVY_HOSTS = {
    "tanitjobs.com",
    "weworkremotely.com",
}


@dataclass(frozen=True)
class TextFetchResult:
    url: str
    text: str
    method: str
    status: str
    error: str | None = None


def _host(url: str) -> str:
    return (urlparse(url).netloc or "").lower()


def _delay_normal_s() -> int:
    return int((os.getenv("TEXT_FETCH_DELAY_NORMAL_S") or "10").strip() or "10")


def _delay_cf_s() -> int:
    return int((os.getenv("TEXT_FETCH_DELAY_CF_S") or "60").strip() or "60")


def _max_jobs_env() -> int:
    return int((os.getenv("TEXT_FETCH_MAX_JOBS") or "50").strip() or "50")


def _delay_for_url(url: str) -> int:
    host = _host(url)
    if any(h in host for h in CF_HEAVY_HOSTS):
        return _delay_cf_s()
    return _delay_normal_s()


def _clean_text(text: str) -> str:
    return re.sub(r"\s+", " ", text or "").strip()


def _is_blocked_text(text: str) -> bool:
    t = (text or "").lower()
    if not t:
        return False
    blocked_markers = [
        "just a moment",
        "verifying you are human",
        "cloudflare",
        "access denied",
        "blocked",
        "captcha",
    ]
    return any(m in t for m in blocked_markers)


def _classify_text(text: str) -> str:
    if _is_blocked_text(text):
        return "blocked"
    if not text or len(text) < 200:
        return "empty"
    return "ok"


def _http_seems_cloudflare(url: str, timeout_s: int = 4) -> bool:
    try:
        resp = requests.head(url, headers={"User-Agent": DEFAULT_UA}, timeout=timeout_s, allow_redirects=True)
    except Exception:
        return False
    server = (resp.headers.get("server") or "").lower()
    if "cloudflare" in server:
        return True
    if any(h.lower().startswith("cf-") for h in resp.headers.keys()):
        return True
    return False


def _fetch_http(url: str, timeout_s: int = 20, max_chars: int = 8000) -> TextFetchResult:
    try:
        resp = requests.get(url, headers={"User-Agent": DEFAULT_UA}, timeout=timeout_s)
    except Exception as e:
        return TextFetchResult(url=url, text="", method="http", status="error", error=str(e))

    status_code = resp.status_code
    if status_code in {403, 429, 503, 520, 522, 523, 524}:
        return TextFetchResult(url=url, text="", method="http", status="blocked", error=f"http {status_code}")
    if status_code >= 400:
        return TextFetchResult(url=url, text="", method="http", status="error", error=f"http {status_code}")

    html = resp.text or ""

    text: Optional[str] = None
    if HTMLParser is not None:
        tree = HTMLParser(html)
        for node in tree.css("script, style, noscript"):
            node.decompose()
        if tree.body is not None:
            text = tree.body.text(separator=" ")
        else:
            text = tree.text(separator=" ")
    else:
        text = re.sub(r"<[^>]+>", " ", html)

    text = _clean_text(text or "")
    if max_chars and len(text) > max_chars:
        text = text[:max_chars]

    status = _classify_text(text)
    return TextFetchResult(url=url, text=text, method="http", status=status)


def _fetch_cdp(url: str, cdp_url: Optional[str]) -> TextFetchResult:
    if not cdp_url:
        return TextFetchResult(url=url, text="", method="cdp", status="error", error="CDP_URL not set")

    try:
        if "tanitjobs.com" in _host(url):
            text = fetch_tanitjobs_page_text(url, cdp_url)
        else:
            text = fetch_page_text_via_cdp(url, cdp_url)
    except Exception as e:
        return TextFetchResult(url=url, text="", method="cdp", status="error", error=str(e))

    status = _classify_text(text)
    return TextFetchResult(url=url, text=text or "", method="cdp", status=status)


def _cdp_first(url: str) -> bool:
    host = _host(url)
    if "tanitjobs.com" in host:
        return True
    if "weworkremotely.com" in host:
        return _http_seems_cloudflare(url)
    return False


def extract_text_for_urls(
    *,
    urls: Iterable[str],
    db_path: str,
    cdp_url: Optional[str] = None,
    max_jobs: Optional[int] = None,
    refresh: bool = False,
) -> dict:
    urls = [u for u in urls if u]
    if not urls:
        return {"candidates": 0, "fetched": 0, "ok": 0, "blocked": 0, "empty": 0, "error": 0, "errors": 0}

    # Dedupe while preserving order
    seen = set()
    deduped = []
    for u in urls:
        if u in seen:
            continue
        seen.add(u)
        deduped.append(u)
    urls = deduped

    max_jobs = _max_jobs_env() if max_jobs is None else max_jobs
    if max_jobs and len(urls) > max_jobs:
        urls = urls[:max_jobs]

    db = JobTextCacheDB(db_path)
    url_canons = [canonicalize_url(u) for u in urls]
    existing = db.get_many(url_canons)

    if not refresh:
        filtered = []
        for u in urls:
            row = existing.get(canonicalize_url(u))
            if row and row.get("status") == "ok" and (row.get("text") or ""):
                continue
            filtered.append(u)
        urls = filtered

    if not urls:
        db.close()
        return {"candidates": 0, "fetched": 0, "ok": 0, "blocked": 0, "empty": 0, "error": 0, "errors": 0}

    cdp_url = cdp_url or (os.getenv("CDP_URL") or "").strip() or None

    cdp_first_urls = [u for u in urls if _cdp_first(u)]
    http_first_urls = [u for u in urls if u not in set(cdp_first_urls)]

    stats = {"candidates": len(urls), "fetched": 0, "ok": 0, "blocked": 0, "empty": 0, "error": 0}

    def _record(res: TextFetchResult) -> None:
        uc = canonicalize_url(res.url)
        db.upsert(url_canon=uc, url=res.url, text=res.text, method=res.method, status=res.status, error=res.error)
        stats["fetched"] += 1
        if res.status in stats:
            stats[res.status] += 1

    # CDP-first sequential
    for i, url in enumerate(cdp_first_urls):
        res = _fetch_cdp(url, cdp_url)
        _record(res)
        if i < len(cdp_first_urls) - 1:
            time.sleep(_delay_for_url(url))

    # HTTP-first with concurrency=2
    http_results = {}
    with ThreadPoolExecutor(max_workers=2) as pool:
        futs = {}
        for i, url in enumerate(http_first_urls):
            if i > 0:
                time.sleep(_delay_for_url(url))
            futs[pool.submit(_fetch_http, url)] = url
        for fut in as_completed(futs):
            url = futs[fut]
            try:
                http_results[url] = fut.result()
            except Exception as e:
                http_results[url] = TextFetchResult(url=url, text="", method="http", status="error", error=str(e))

    fallback_urls = []
    for url, res in http_results.items():
        if res.status == "ok" or not cdp_url:
            _record(res)
        else:
            fallback_urls.append(url)

    # CDP fallback sequential
    for i, url in enumerate(fallback_urls):
        res = _fetch_cdp(url, cdp_url)
        _record(res)
        if i < len(fallback_urls) - 1:
            time.sleep(_delay_for_url(url))

    db.close()
    return {**stats, "errors": stats.get("error", 0)}


def extract_text_for_sheet(
    *,
    sheet_cfg: SheetsConfig,
    db_path: str,
    max_jobs: Optional[int] = None,
    refresh: bool = False,
    verbose: bool = False,
) -> dict:
    rows = _get_sheet_rows(sheet_cfg)
    if not rows or len(rows) < 2:
        return {"candidates": 0, "fetched": 0, "ok": 0, "blocked": 0, "empty": 0, "error": 0, "errors": 0}

    # Prefer extracting text for rows that still need scoring (llm_score empty).
    urls = []
    # Process most-recent rows first (sheet appends at the bottom).
    for r in reversed(rows[1:]):
        if len(r) < 7:
            continue
        url = (r[6] or "").strip()
        llm_score = (r[9] or "").strip() if len(r) > 9 else ""
        if url and not llm_score:
            urls.append(url)

    summary = extract_text_for_urls(urls=urls, db_path=db_path, max_jobs=max_jobs, refresh=refresh)
    if verbose:
        # Best-effort: show a quick sample of recent cache entries.
        try:
            import sqlite3
            conn = sqlite3.connect(db_path)
            conn.row_factory = sqlite3.Row
            rows2 = conn.execute(
                "select url, status, method, length(text) as len, fetched_at from job_text_cache order by fetched_at desc limit 10"
            ).fetchall()
            for r in rows2:
                print(f"cache: {r['status']} {r['method']} len={r['len']} url={str(r['url'])[:120]}")
            conn.close()
        except Exception:
            pass
    return summary
