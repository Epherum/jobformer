from __future__ import annotations

import json
import sqlite3
from dataclasses import dataclass
from pathlib import Path
from typing import List, Tuple

from .gog import run_gog

import requests

from .alerts.pushover import load_from_envfile
from .config import AppConfig


@dataclass(frozen=True)
class CheckResult:
    name: str
    ok: bool
    detail: str = ""


def _run_gog(args: List[str]) -> Tuple[int, str]:
    p = run_gog(args, check=False)
    out = (p.stdout or "") + ("\n" + p.stderr if p.stderr else "")
    return p.returncode, out.strip()


def smoke_checks(cfg: AppConfig) -> list[CheckResult]:
    results: list[CheckResult] = []

    # SQLite
    db_path = Path("data/jobs.sqlite3")
    if not db_path.exists():
        results.append(CheckResult("sqlite", False, f"missing {db_path}"))
    else:
        try:
            con = sqlite3.connect(str(db_path))
            cur = con.cursor()
            cur.execute("SELECT COUNT(*) FROM jobs")
            n = cur.fetchone()[0]
            con.close()
            results.append(CheckResult("sqlite", True, f"jobs={n}"))
        except Exception as e:
            results.append(CheckResult("sqlite", False, f"error: {e}"))

    # CDP
    try:
        r = requests.get(f"{cfg.cdp_url}/json/version", timeout=3)
        r.raise_for_status()
        j = r.json()
        results.append(CheckResult("cdp", True, j.get("Browser", "ok")))
    except Exception as e:
        results.append(CheckResult("cdp", False, f"{e}"))

    # Pushover env
    po = load_from_envfile()
    results.append(CheckResult("pushover", bool(po), "configured" if po else "missing data/pushover.env"))

    # Sheet access
    if not cfg.sheet_id:
        results.append(CheckResult("sheets", False, "missing SHEET_ID"))
    else:
        code, out = _run_gog([
            "gog",
            "sheets",
            "get",
            cfg.sheet_id,
            f"{cfg.jobs_tab}!A1:A1",
            "--account",
            cfg.sheet_account,
            "--json",
        ])
        if code == 0:
            results.append(CheckResult("sheets", True, "ok"))
        else:
            results.append(CheckResult("sheets", False, out.splitlines()[-1] if out else "error"))

    return results
