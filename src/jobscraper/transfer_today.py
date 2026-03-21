from __future__ import annotations

import json
from dataclasses import dataclass
from typing import List, Sequence

from .gog import run_gog


@dataclass
class TransferConfig:
    sheet_id: str
    from_tabs: list[str]
    to_tab: str = "Jobs"
    account: str = "wassimfekih2@gmail.com"
    range_cols: str = "A:J"


def _run_gog(args: List[str]) -> str:
    proc = run_gog(args, check=True)
    return proc.stdout


def fetch_rows_for_tab(cfg: TransferConfig, tab: str) -> list[list[str]]:
    out = _run_gog([
        "gog", "sheets", "get", cfg.sheet_id, f"{tab}!{cfg.range_cols}", "--account", cfg.account, "--json"
    ])
    data = json.loads(out)
    values = data.get("values") or []
    if not values or len(values) <= 1:
        return []
    rows = values[1:]
    norm: list[list[str]] = []
    for r in rows:
        r = list(r)
        if len(r) < 10:
            r = r + [""] * (10 - len(r))
        norm.append(r[:10])
    return norm


def fetch_rows(cfg: TransferConfig) -> list[list[str]]:
    all_rows: list[list[str]] = []
    seen_urls: set[str] = set()
    for tab in cfg.from_tabs:
        for r in fetch_rows_for_tab(cfg, tab):
            url = r[6] if len(r) > 6 else ""
            if url and url in seen_urls:
                continue
            if url:
                seen_urls.add(url)
            all_rows.append(r)
    return all_rows


def append_rows(cfg: TransferConfig, rows: list[list[str]]) -> int:
    if not rows:
        return 0
    _run_gog([
        "gog", "sheets", "append", cfg.sheet_id, f"{cfg.to_tab}!{cfg.range_cols}", "--account", cfg.account,
        "--values-json", json.dumps(rows, ensure_ascii=False), "--insert", "INSERT_ROWS"
    ])
    return len(rows)


def clear_tabs(cfg: TransferConfig) -> None:
    for tab in cfg.from_tabs:
        _run_gog(["gog", "sheets", "clear", cfg.sheet_id, f"{tab}!A2:Z", "--account", cfg.account])


def transfer_today(cfg: TransferConfig) -> int:
    rows = fetch_rows(cfg)
    n = append_rows(cfg, rows)
    if n:
        clear_tabs(cfg)
    return n
