from __future__ import annotations

import json
import subprocess
from dataclasses import dataclass
from typing import List


@dataclass
class TransferConfig:
    sheet_id: str
    from_tab: str = "Jobs_Today"
    to_tab: str = "Jobs"
    account: str = "wassimfekih2@gmail.com"
    # Keep range wide enough for our current Jobs schema (A:I)
    range_cols: str = "A:I"


def _run_gog(args: List[str]) -> str:
    proc = subprocess.run(args, capture_output=True, text=True)
    if proc.returncode != 0:
        raise RuntimeError(f"gog failed: {' '.join(args)}\n{proc.stderr}\n{proc.stdout}")
    return proc.stdout


def fetch_rows(cfg: TransferConfig) -> list[list[str]]:
    out = _run_gog(
        [
            "gog",
            "sheets",
            "get",
            cfg.sheet_id,
            f"{cfg.from_tab}!{cfg.range_cols}",
            "--account",
            cfg.account,
            "--json",
        ]
    )
    data = json.loads(out)
    values = data.get("values") or []

    # values includes header row at index 0 if present
    if not values or len(values) <= 1:
        return []

    rows = values[1:]
    # normalize row length to 9 cols
    norm: list[list[str]] = []
    for r in rows:
        r = list(r)
        if len(r) < 9:
            r = r + [""] * (9 - len(r))
        norm.append(r[:9])
    return norm


def append_rows(cfg: TransferConfig, rows: list[list[str]]) -> int:
    if not rows:
        return 0

    _run_gog(
        [
            "gog",
            "sheets",
            "append",
            cfg.sheet_id,
            f"{cfg.to_tab}!{cfg.range_cols}",
            "--account",
            cfg.account,
            "--values-json",
            json.dumps(rows, ensure_ascii=False),
            "--insert",
            "INSERT_ROWS",
        ]
    )
    return len(rows)


def clear_from(cfg: TransferConfig) -> None:
    _run_gog(["gog", "sheets", "clear", cfg.sheet_id, f"{cfg.from_tab}!A2:Z", "--account", cfg.account])


def transfer_today(cfg: TransferConfig) -> int:
    rows = fetch_rows(cfg)
    n = append_rows(cfg, rows)
    if n:
        clear_from(cfg)
    return n
