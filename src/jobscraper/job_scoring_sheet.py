from __future__ import annotations

import os
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path
from typing import Optional

from .job_scores_db import JobScoresDB
from .llm_score import LLMScore, score_job_with_ollama
from .sheets_sync import SheetsConfig, _get_sheet_rows, update_job_scores
from .job_scoring import ScoreCandidate, ScoreResult, _score_one


def score_unscored_sheet_rows(
    *,
    db_path: Path,
    model: str,
    sheet_cfg: SheetsConfig,
    max_jobs: int = 25,
    concurrency: int = 1,
) -> dict:
    """Score rows in a sheet tab (Jobs_Today) that have empty llm_score.

    This is the most reliable way to ensure scores appear in Jobs_Today.
    """

    rows = _get_sheet_rows(sheet_cfg)
    if not rows or len(rows) < 2:
        return {"candidates": 0, "scored": 0, "updated_rows": 0, "errors": 0}

    # Header indices: url is col G (index 6), llm_score is col J (index 9)
    # We do NOT store llm_model anymore.
    candidates: list[ScoreCandidate] = []
    for r in rows[1:]:
        if len(r) < 7:
            continue
        url = r[6].strip()
        llm_score = r[8].strip() if len(r) > 8 and r[8] is not None else ""
        if not url or llm_score:
            continue

        title = r[2].strip() if len(r) > 2 else ""
        company = r[3].strip() if len(r) > 3 else ""
        location = r[4].strip() if len(r) > 4 else ""
        candidates.append(ScoreCandidate(title=title, company=company, location=location, url=url))

    if max_jobs and len(candidates) > max_jobs:
        candidates = candidates[:max_jobs]

    cdp_url = (os.getenv("CDP_URL") or "").strip() or None

    results: list[ScoreResult] = []
    failures: list[ScoreCandidate] = []
    errors = 0

    with ThreadPoolExecutor(max_workers=max(1, concurrency)) as pool:
        futs = {pool.submit(_score_one, c, model, cdp_url): c for c in candidates}
        for fut in as_completed(futs):
            c = futs[fut]
            try:
                res = fut.result()
                if res is None:
                    failures.append(c)
                    continue
                results.append(res)
            except Exception:
                errors += 1
                failures.append(c)

    # Save to DB cache
    scores_db = JobScoresDB(db_path)
    for r in results:
        scores_db.upsert_score(url=r.url, score=r.score, decision=r.decision, reasons=r.reasons, model=r.model)
    scores_db.close()

    updated_rows = 0
    sheet_updates = []

    # Successful scores
    for r in results:
        sheet_updates.append(
            {
                "url": r.url,
                "score": r.score,
                "decision": r.decision,
                "reasons": (r.reasons[0] if r.reasons else "")[:180],
            }
        )

    # Mark failures so we don't loop forever on un-fetchable pages.
    for c in failures:
        sheet_updates.append(
            {
                "url": c.url,
                "score": 0,
                "decision": "no",
                "reasons": "Could not extract job text (blocked/empty/timeout).",
            }
        )

    if sheet_updates:
        updated_rows = update_job_scores(sheet_cfg, sheet_updates)

    return {
        "candidates": len(candidates),
        "scored": len(results),
        "updated_rows": updated_rows,
        "errors": errors,
        "failed": len(failures),
    }
