from __future__ import annotations

import json
from dataclasses import dataclass
from typing import List, Sequence

from .gog import run_gog
from .models import Job
from .filtering import decision_for_title, match_labels


@dataclass
class SheetsConfig:
    sheet_id: str
    tab: str = "Jobs"
    account: str = "wassimfekih2@gmail.com"


def _run_gog(args: List[str]) -> str:
    proc = run_gog(args, check=True)
    return proc.stdout


def ensure_jobs_header(cfg: SheetsConfig) -> None:
    # Sheet schema (Jobs / Jobs_Today):
    # A: source
    # B: labels
    # C: title
    # D: company
    # E: location
    # F: date_added
    # G: url
    # H: decision
    # I: notes
    # J: llm_score
    # K: llm_decision
    # L: llm_reasons
    values = [[
        "source",
        "labels",
        "title",
        "company",
        "location",
        "date_added",
        "url",
        "decision",
        "notes",
        "llm_score",
        "llm_decision",
        "llm_reasons",
    ]]
    _run_gog(
        [
            "gog",
            "sheets",
            "update",
            cfg.sheet_id,
            f"{cfg.tab}!A1:L1",
            "--account",
            cfg.account,
            "--values-json",
            json.dumps(values, ensure_ascii=False),
            "--input",
            "USER_ENTERED",
        ]
    )


def append_jobs(cfg: SheetsConfig, jobs: Sequence[Job], date_label: str) -> None:
    if not jobs:
        return

    rows = []
    for j in jobs:
        labels = ",".join(match_labels(j.title))
        decision = decision_for_title(j.title)
        rows.append(
            [
                j.source,
                labels,
                j.title,
                j.company,
                j.location,
                date_label,
                j.url,
                decision,
                "",
                "",
                "",
                "",
            ]
        )

    _run_gog(
        [
            "gog",
            "sheets",
            "append",
            cfg.sheet_id,
            f"{cfg.tab}!A:L",
            "--account",
            cfg.account,
            "--values-json",
            json.dumps(rows, ensure_ascii=False),
            "--insert",
            "INSERT_ROWS",
        ]
    )


def _get_sheet_rows(cfg: SheetsConfig, range_cols: str = "A:L") -> list[list[str]]:
    out = _run_gog(
        [
            "gog",
            "sheets",
            "get",
            cfg.sheet_id,
            f"{cfg.tab}!{range_cols}",
            "--account",
            cfg.account,
            "--json",
        ]
    )
    data = json.loads(out)
    values = data.get("values") or []
    return values


def find_rows_by_url(cfg: SheetsConfig, urls: Sequence[str]) -> dict[str, int]:
    if not urls:
        return {}
    url_set = set(urls)
    values = _get_sheet_rows(cfg)
    if not values:
        return {}

    out: dict[str, int] = {}
    # row 1 is header
    for i, row in enumerate(values[1:], start=2):
        if len(row) <= 6:
            continue
        url = row[6]
        if url in url_set:
            out[url] = i
    return out


def update_job_scores(cfg: SheetsConfig, updates: Sequence[dict]) -> int:
    """Update Jobs/Jobs_Today rows by URL with LLM score fields (J:L).

    updates: list of {url, score, decision, reasons}
    Returns number of rows updated in the sheet.
    """

    if not updates:
        return 0

    url_to_row = find_rows_by_url(cfg, [u["url"] for u in updates])
    if not url_to_row:
        return 0

    # Map row -> values
    row_to_values: dict[int, list] = {}
    for u in updates:
        row = url_to_row.get(u["url"])
        if not row:
            continue
        row_to_values[row] = [u.get("score", ""), u.get("decision", ""), u.get("reasons", "")]

    if not row_to_values:
        return 0

    rows_sorted = sorted(row_to_values.keys())

    def _flush_block(start: int, end: int) -> None:
        values = [row_to_values[r] for r in range(start, end + 1)]
        _run_gog(
            [
                "gog",
                "sheets",
                "update",
                cfg.sheet_id,
                f"{cfg.tab}!J{start}:L{end}",
                "--account",
                cfg.account,
                "--values-json",
                json.dumps(values, ensure_ascii=False),
                "--input",
                "USER_ENTERED",
            ]
        )

    # Group contiguous rows into blocks to reduce API calls.
    block_start = rows_sorted[0]
    block_end = rows_sorted[0]
    for r in rows_sorted[1:]:
        if r == block_end + 1:
            block_end = r
            continue
        _flush_block(block_start, block_end)
        block_start = block_end = r

    _flush_block(block_start, block_end)
    return len(row_to_values)
