from __future__ import annotations

import time
from pathlib import Path
from typing import Optional

from .job_scoring_sheet import score_unscored_sheet_rows
from .llm_score import DEFAULT_MODEL
from .sheets_sync import SheetsConfig


def score_all_unscored_sheet_rows(
    *,
    sheet_cfg: SheetsConfig,
    db_path: Path = Path("data") / "jobs.sqlite3",
    model: str = DEFAULT_MODEL,
    batch_size: int = 25,
    max_batches: int = 50,
    sleep_s: float = 0.5,
    progress_cb=None,
) -> dict:
    """Loop scoring Jobs_Today until there are no more unscored rows (or max_batches).

    Returns a summary dict.
    """

    total_scored = 0
    total_updated = 0
    total_errors = 0

    for i in range(max_batches):
        summary = score_unscored_sheet_rows(
            db_path=db_path,
            model=model,
            sheet_cfg=sheet_cfg,
            max_jobs=batch_size,
            concurrency=1,
        )

        batch_candidates = int(summary.get("candidates", 0) or 0)
        batch_scored = int(summary.get("scored", 0) or 0)
        batch_updated = int(summary.get("updated_rows", 0) or 0)
        batch_errors = int(summary.get("errors", 0) or 0)

        total_scored += batch_scored
        total_updated += batch_updated
        total_errors += batch_errors

        if progress_cb:
            progress_cb(i + 1, {"candidates": batch_candidates, "scored": batch_scored, "updated_rows": batch_updated, "errors": batch_errors})

        # Stop when there is nothing left to score.
        if batch_candidates == 0:
            break

        # If we made no progress, stop to avoid infinite looping.
        if batch_scored == 0 and batch_errors == 0:
            break

        if sleep_s:
            time.sleep(sleep_s)

    return {
        "scored": total_scored,
        "updated_rows": total_updated,
        "errors": total_errors,
    }
