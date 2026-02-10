from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

from .job_scores_db import JobScoresDB
from .job_text_cache_db import JobTextCacheDB
from .url_canon import canonicalize_url
from .llm_score import LLMScore, score_job_with_ollama
from .sheets_sync import SheetsConfig, _get_sheet_rows, update_job_scores
from .text_extraction import extract_text_for_urls


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
    reasons: list[str]
    model: str


def _score_from_text(candidate: ScoreCandidate, text: str, model: str) -> Optional[ScoreResult]:
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


def score_unscored_sheet_rows_from_cache(
    *,
    db_path: Path,
    model: str,
    sheet_cfg: SheetsConfig,
    max_jobs: int = 25,
    concurrency: int = 2,
    extract_missing: bool = False,
) -> dict:
    rows = _get_sheet_rows(sheet_cfg)
    if not rows or len(rows) < 2:
        return {"candidates": 0, "scored": 0, "updated_rows": 0, "errors": 0, "missing": 0}

    candidates: list[ScoreCandidate] = []
    # Process most-recent rows first (sheet appends at the bottom).
    for r in reversed(rows[1:]):
        if len(r) < 7:
            continue
        url = (r[6] or "").strip()
        llm_score = r[9].strip() if len(r) > 9 and r[9] is not None else ""
        if not url or llm_score:
            continue

        title = r[2].strip() if len(r) > 2 else ""
        company = r[3].strip() if len(r) > 3 else ""
        location = r[4].strip() if len(r) > 4 else ""
        candidates.append(ScoreCandidate(title=title, company=company, location=location, url=url))

    if max_jobs and len(candidates) > max_jobs:
        candidates = candidates[:max_jobs]

    urls = [c.url for c in candidates]
    url_canons = [canonicalize_url(u) for u in urls]
    cache_db = JobTextCacheDB(db_path)
    cached = cache_db.get_many(url_canons)

    missing_urls = [u for u in urls if (not cached.get(canonicalize_url(u)) or cached.get(canonicalize_url(u), {}).get("status") != "ok")]

    if extract_missing and missing_urls:
        extract_text_for_urls(urls=missing_urls, db_path=str(db_path))
        cached = cache_db.get_many([canonicalize_url(u) for u in urls])

    results: list[ScoreResult] = []
    errors = 0
    missing = 0

    def _text_for_url(u: str) -> str:
        row = cached.get(canonicalize_url(u))
        if not row:
            return ""
        if row.get("status") != "ok":
            return ""
        return (row.get("text") or "")

    with ThreadPoolExecutor(max_workers=max(1, concurrency)) as pool:
        futs = {}
        for c in candidates:
            text = _text_for_url(c.url)
            if not text:
                missing += 1
                continue
            futs[pool.submit(_score_from_text, c, text, model)] = c

        for fut in as_completed(futs):
            c = futs[fut]
            try:
                res = fut.result()
                if res is None:
                    continue
                results.append(res)
            except Exception as e:
                errors += 1
                # Best-effort: print which URL failed so debugging is possible.
                print(f"score_error url={c.url} title={c.title[:60]} err={type(e).__name__}: {e}")

    scores_db = JobScoresDB(db_path)
    for r in results:
        scores_db.upsert_score(url=r.url, score=r.score, decision=r.decision, reasons=r.reasons, model=r.model)
    scores_db.close()

    updated_rows = 0
    if results:
        sheet_updates = []
        for r in results:
            sheet_updates.append(
                {
                    "url": r.url,
                    "score": r.score,
                    "decision": r.decision,
                    "reasons": (r.reasons[0] if r.reasons else "")[:180],
                }
            )
        updated_rows = update_job_scores(sheet_cfg, sheet_updates)

    cache_db.close()

    return {
        "candidates": len(candidates),
        "scored": len(results),
        "updated_rows": updated_rows,
        "errors": errors,
        "missing": missing,
    }
