from __future__ import annotations

import json
from dataclasses import dataclass
from typing import List, Sequence

from .gog import run_gog
from .models import Job
from .filtering import decision_for_title, match_labels, DECISION_TOO_SENIOR


@dataclass
class SheetsConfig:
    sheet_id: str
    tab: str = "Jobs"
    account: str = "wassimfekih2@gmail.com"


def _run_gog(args: List[str]) -> str:
    proc = run_gog(args, check=True)
    return proc.stdout


def ensure_jobs_header(cfg: SheetsConfig) -> None:
    values = [["source","labels","title","company","location","date_added","url","decision","score","reason","feedback","suggested_decision"]]
    _run_gog(["gog","sheets","update",cfg.sheet_id,f"{cfg.tab}!A1:L1","--account",cfg.account,"--values-json",json.dumps(values, ensure_ascii=False),"--input","USER_ENTERED"])


def append_jobs(cfg: SheetsConfig, jobs: Sequence[Job], date_label: str) -> None:
    if not jobs:
        return
    rows = []
    for j in jobs:
        labels = ",".join(match_labels(j.title))
        decision = decision_for_title(j.title)
        rows.append([j.source, labels, j.title, j.company, j.location, date_label, j.url, decision, "", "", "", ""])
    _run_gog(["gog","sheets","append",cfg.sheet_id,f"{cfg.tab}!A:L","--account",cfg.account,"--values-json",json.dumps(rows, ensure_ascii=False),"--insert","INSERT_ROWS"])


def append_jobs_routed(sheet_id: str, account: str, jobs: Sequence[Job], date_label: str, sales_tab: str, tech_tab: str, jobs_tab: str = "Jobs") -> dict:
    sales_jobs = []
    tech_jobs = []
    oversenior_jobs = []
    for j in jobs:
        if decision_for_title(j.title) == DECISION_TOO_SENIOR:
            oversenior_jobs.append(j)
            continue
        labels = set(match_labels(j.title))
        if 'SALES' in labels:
            sales_jobs.append(j)
        if 'TECH' in labels or 'DATA' in labels or 'AI' in labels:
            tech_jobs.append(j)
    if sales_jobs:
        cfg = SheetsConfig(sheet_id=sheet_id, tab=sales_tab, account=account)
        ensure_jobs_header(cfg)
        append_jobs(cfg, sales_jobs, date_label)
    if tech_jobs:
        cfg = SheetsConfig(sheet_id=sheet_id, tab=tech_tab, account=account)
        ensure_jobs_header(cfg)
        append_jobs(cfg, tech_jobs, date_label)
    if oversenior_jobs:
        cfg = SheetsConfig(sheet_id=sheet_id, tab=jobs_tab, account=account)
        ensure_jobs_header(cfg)
        append_jobs(cfg, oversenior_jobs, date_label)
    return {"sales": len(sales_jobs), "tech": len(tech_jobs), "oversenior": len(oversenior_jobs)}


def _get_sheet_rows(cfg: SheetsConfig, range_cols: str = "A:L") -> list[list[str]]:
    out = _run_gog(["gog","sheets","get",cfg.sheet_id,f"{cfg.tab}!{range_cols}","--account",cfg.account,"--json"])
    data = json.loads(out)
    return data.get("values") or []


def find_rows_by_url(cfg: SheetsConfig, urls: Sequence[str]) -> dict[str, int]:
    if not urls:
        return {}
    url_set = set(urls)
    values = _get_sheet_rows(cfg)
    out: dict[str, int] = {}
    for i, row in enumerate(values[1:], start=2):
        if len(row) <= 6:
            continue
        url = row[6]
        if url in url_set:
            out[url] = i
    return out


def update_job_scores(cfg: SheetsConfig, updates: Sequence[dict]) -> int:
    if not updates:
        return 0
    url_to_row = find_rows_by_url(cfg, [u["url"] for u in updates])
    if not url_to_row:
        return 0

    # Preserve existing feedback in column K. Updates should touch:
    # I=score, J=reason, K=feedback(existing), L=suggested_decision.
    existing_rows = _get_sheet_rows(cfg)
    row_index_to_feedback: dict[int, str] = {}
    for i, row in enumerate(existing_rows[1:], start=2):
        if len(row) > 10 and row[10] is not None:
            row_index_to_feedback[i] = row[10]
        else:
            row_index_to_feedback[i] = ""

    row_to_values: dict[int, list] = {}
    for u in updates:
        row = url_to_row.get(u["url"])
        if not row:
            continue
        existing_feedback = row_index_to_feedback.get(row, "")
        row_to_values[row] = [
            u.get("score", ""),
            u.get("reasons", ""),
            existing_feedback,
            u.get("suggested_decision", ""),
        ]
    if not row_to_values:
        return 0
    rows_sorted = sorted(row_to_values.keys())
    def _flush_block(start: int, end: int) -> None:
        values = [row_to_values[r] for r in range(start, end + 1)]
        _run_gog(["gog","sheets","update",cfg.sheet_id,f"{cfg.tab}!I{start}:L{end}","--account",cfg.account,"--values-json",json.dumps(values, ensure_ascii=False),"--input","USER_ENTERED"])
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
