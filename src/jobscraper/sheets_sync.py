from __future__ import annotations

import json
import subprocess
from dataclasses import dataclass
from typing import List, Sequence

from .models import Job
from .filtering import match_labels


@dataclass
class SheetsConfig:
    sheet_id: str
    tab: str = "Jobs"
    account: str = "wassimfekih2@gmail.com"


def _run_gog(args: List[str]) -> None:
    proc = subprocess.run(args, capture_output=True, text=True)
    if proc.returncode != 0:
        raise RuntimeError(f"gog failed: {' '.join(args)}\n{proc.stderr}\n{proc.stdout}")


def ensure_jobs_header(cfg: SheetsConfig) -> None:
    # Dedicated Jobs tab. Header always at row 1.
    # Workflow columns are on the right so you can track applications.
    values = [[
        "date_added",
        "source",
        "title",
        "company",
        "location",
        "url",
        "labels",
        "decision",
        "notes",
    ]]
    _run_gog(
        [
            "gog",
            "sheets",
            "update",
            cfg.sheet_id,
            f"{cfg.tab}!A1:I1",
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
        # decision/decision_at/notes intentionally left blank; managed in Sheets.
        rows.append([date_label, j.source, j.title, j.company, j.location, j.url, labels, "", ""]) 

    _run_gog(
        [
            "gog",
            "sheets",
            "append",
            cfg.sheet_id,
            f"{cfg.tab}!A:I",
            "--account",
            cfg.account,
            "--values-json",
            json.dumps(rows, ensure_ascii=False),
            "--insert",
            "INSERT_ROWS",
        ]
    )
