from __future__ import annotations

import datetime as dt
import os
import sqlite3
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable, List, Optional
from urllib.parse import urlparse

from .filtering import is_relevant
from .job_scores_db import JobScoresDB
from .llm_score import LLMScore, score_job_with_ollama
from .page_fetch import fetch_page_text
from .cdp_page_fetch import fetch_page_text_via_cdp
from .sheets_sync import SheetsConfig, _get_sheet_rows, update_job_scores
from .linkedin_page_fetch import cdp_reachable, fetch_linkedin_page_text
from .tanitjobs_page_fetch import fetch_tanitjobs_page_text


@dataclass(frozen=True)
class ScoreCandidate:
    title: str
    company: str
    location: str
    url: str


@dataclass(frozen=True)
class ScoreResult:
    url: str
    score: float
    decision: str
    reasons: List[str]
    model: str


def _iso(ts: float) -> str:
    return dt.datetime.fromtimestamp(ts, tz=dt.timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")


def _fetch_recent_jobs(db_path: Path, start_ts: float, end_ts: float) -> list[ScoreCandidate]:
    start_iso = _iso(start_ts)
    end_iso = _iso(end_ts)

    conn = sqlite3.connect(str(db_path))
    conn.row_factory = sqlite3.Row
    cur = conn.execute(
        """
        SELECT title, company, location, url
        FROM jobs
        WHERE (first_seen_at BETWEEN ? AND ?)
           OR (last_seen_at BETWEEN ? AND ?)
        """,
        (start_iso, end_iso, start_iso, end_iso),
    )
    rows = cur.fetchall()
    conn.close()

    out: list[ScoreCandidate] = []
    for r in rows:
        out.append(ScoreCandidate(title=r["title"], company=r["company"], location=r["location"], url=r["url"]))
    return out


def _is_linkedin(url: str) -> bool:
    host = (urlparse(url).netloc or "").lower()
    return "linkedin.com" in host


def _is_tanit(url: str) -> bool:
    host = (urlparse(url).netloc or "").lower()
    return "tanitjobs.com" in host


def _score_one(candidate: ScoreCandidate, model: str, cdp_url: Optional[str]) -> Optional[ScoreResult]:
    if _is_linkedin(candidate.url):
        text = fetch_linkedin_page_text(candidate.url, cdp_url)
    elif _is_tanit(candidate.url):
        # Tanitjobs may be Cloudflare-protected; prefer CDP session if available.
        text = fetch_tanitjobs_page_text(candidate.url, cdp_url)
        if not text:
            text = fetch_page_text(candidate.url)
    else:
        text = fetch_page_text(candidate.url)
        # Generic fallback for Cloudflare/blocked sites: use CDP to render and extract.
        if not text and cdp_url:
            text = fetch_page_text_via_cdp(candidate.url, cdp_url)
    if not text:
        return None
    llm: LLMScore = score_job_with_ollama(
        title=candidate.title,
        company=candidate.company,
        location=candidate.location,
        url=candidate.url,
        page_text=text,
        model=model,
    )
    return ScoreResult(
        url=candidate.url,
        score=llm.score,
        decision=llm.decision,
        reasons=llm.reasons,
        model=llm.model,
    )


def score_recent_jobs(
    *,
    db_path: Path,
    start_ts: float,
    end_ts: float,
    model: str,
    sheet_cfg: Optional[SheetsConfig] = None,
    update_sheet: bool = True,
    max_jobs: int = 50,
    concurrency: int = 2,
) -> dict:
    """Score recent relevant jobs and optionally update the sheet.

    Returns a summary dict.
    """

    candidates = _fetch_recent_jobs(db_path, start_ts, end_ts)

    cdp_url = (os.getenv("CDP_URL") or "").strip() or None
    cdp_ok: Optional[bool] = None

    def _ensure_cdp_ok() -> bool:
        nonlocal cdp_ok
        if cdp_ok is None:
            cdp_ok = bool(cdp_url) and cdp_reachable(cdp_url)
        return cdp_ok

    # Filter relevant, dedupe by url.
    seen = set()
    filtered: list[ScoreCandidate] = []
    linkedin_skipped = 0
    for c in candidates:
        if not c.url or c.url in seen:
            continue
        seen.add(c.url)
        if not is_relevant(c.title):
            continue
        if _is_linkedin(c.url):
            if not _ensure_cdp_ok():
                linkedin_skipped += 1
                continue
        filtered.append(c)

    if linkedin_skipped and not cdp_ok:
        print(
            "Skipping LinkedIn scoring: CDP_URL not set/reachable. "
            "Start an authenticated Chrome with --remote-debugging-port and set CDP_URL."
        )

    scores_db = JobScoresDB(db_path)
    existing = scores_db.get_many([c.url for c in filtered])
    to_score = [c for c in filtered if c.url not in existing]

    # Cap the number of NEW jobs we score (do not waste the cap on already-scored URLs).
    if max_jobs and len(to_score) > max_jobs:
        to_score = to_score[:max_jobs]

    results: list[ScoreResult] = []
    errors = 0

    with ThreadPoolExecutor(max_workers=max(1, concurrency)) as pool:
        futs = {pool.submit(_score_one, c, model, cdp_url): c for c in to_score}
        for fut in as_completed(futs):
            try:
                res = fut.result()
                if res is None:
                    continue
                results.append(res)
            except Exception:
                errors += 1

    for r in results:
        scores_db.upsert_score(url=r.url, score=r.score, decision=r.decision, reasons=r.reasons, model=r.model)
    scores_db.close()

    updated_rows = 0
    if update_sheet and sheet_cfg and results:
        sheet_updates = []
        for r in results:
            sheet_updates.append(
                {
                    "url": r.url,
                    "score": r.score,
                    "decision": r.decision,
                    # Keep sheet readable: 1 short line only.
                    "reasons": (r.reasons[0] if r.reasons else "")[:180],
                }
            )
        updated_rows = update_job_scores(sheet_cfg, sheet_updates)

    return {
        "candidates": len(candidates),
        "filtered": len(filtered),
        "scored": len(results),
        "skipped_existing": len(filtered) - len(to_score),
        "errors": errors,
        "updated_rows": updated_rows,
        "linkedin_skipped": linkedin_skipped,
    }
