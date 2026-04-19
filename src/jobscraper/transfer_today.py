from __future__ import annotations

import json
from dataclasses import dataclass
from typing import List

from .gog import run_gog


@dataclass
class TransferConfig:
    sheet_id: str
    from_tabs: list[str]
    to_tab: str = "Jobs"
    applied_tab: str = "Applied Jobs"
    account: str = "wassimfekih3@gmail.com"
    range_cols: str = "A:M"
    decision_col: int = 8  # 1-indexed. H = decision.
    applied_value: str = "APPLIED"


def _run_gog(args: List[str]) -> str:
    proc = run_gog(args, check=True)
    return proc.stdout


def _range_width(range_cols: str) -> int:
    _, end = range_cols.split(":", 1)
    end = end.strip().upper()
    width = 0
    for ch in end:
        if not ("A" <= ch <= "Z"):
            continue
        width = width * 26 + (ord(ch) - ord("A") + 1)
    if width <= 0:
        raise ValueError(f"Unsupported range_cols: {range_cols}")
    return width


def _get_tab_values(cfg: TransferConfig, tab: str) -> list[list[str]]:
    out = _run_gog([
        "gog", "sheets", "get", cfg.sheet_id, f"{tab}!{cfg.range_cols}", "--account", cfg.account, "--json"
    ])
    data = json.loads(out)
    return data.get("values") or []


def _normalize_rows(rows: list[list[str]], width: int) -> list[list[str]]:
    norm: list[list[str]] = []
    for r in rows:
        r = list(r)
        if len(r) < width:
            r = r + [""] * (width - len(r))
        norm.append(r[:width])
    return norm


def fetch_header_for_tab(cfg: TransferConfig, tab: str) -> list[str]:
    values = _get_tab_values(cfg, tab)
    if not values:
        return []
    width = _range_width(cfg.range_cols)
    return _normalize_rows([values[0]], width)[0]


def fetch_rows_for_tab(cfg: TransferConfig, tab: str) -> list[list[str]]:
    values = _get_tab_values(cfg, tab)
    if not values or len(values) <= 1:
        return []
    width = _range_width(cfg.range_cols)
    return _normalize_rows(values[1:], width)


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


def split_rows(cfg: TransferConfig, rows: list[list[str]]) -> tuple[list[list[str]], list[list[str]]]:
    regular_rows: list[list[str]] = []
    applied_rows: list[list[str]] = []
    decision_idx = max(0, cfg.decision_col - 1)
    applied_value = cfg.applied_value.strip().upper()

    for r in rows:
        decision = ""
        if len(r) > decision_idx and r[decision_idx] is not None:
            decision = str(r[decision_idx]).strip().upper()
        if decision == applied_value:
            applied_rows.append(r)
        else:
            regular_rows.append(r)

    return regular_rows, applied_rows


def ensure_tab_exists(cfg: TransferConfig, tab: str, header: list[str] | None = None) -> None:
    try:
        values = _get_tab_values(cfg, tab)
    except RuntimeError as e:
        msg = str(e)
        if "Unable to parse range" not in msg and "Range" not in msg and "tab" not in msg.lower():
            raise
        _run_gog(["gog", "sheets", "add-tab", cfg.sheet_id, tab, "--account", cfg.account])
        values = []

    if header and (not values or not any(str(v).strip() for v in values[0])):
        _run_gog([
            "gog", "sheets", "update", cfg.sheet_id, f"{tab}!{cfg.range_cols.split(':', 1)[0]}1:{cfg.range_cols.split(':', 1)[1]}1",
            "--account", cfg.account, "--values-json", json.dumps([header], ensure_ascii=False), "--input", "USER_ENTERED"
        ])


def append_rows(cfg: TransferConfig, tab: str, rows: list[list[str]]) -> int:
    if not rows:
        return 0
    _run_gog([
        "gog", "sheets", "append", cfg.sheet_id, f"{tab}!{cfg.range_cols}", "--account", cfg.account,
        "--values-json", json.dumps(rows, ensure_ascii=False), "--insert", "INSERT_ROWS"
    ])
    return len(rows)


def clear_tabs(cfg: TransferConfig) -> None:
    for tab in cfg.from_tabs:
        _run_gog(["gog", "sheets", "clear", cfg.sheet_id, f"{tab}!A2:Z", "--account", cfg.account])


def transfer_today(cfg: TransferConfig) -> dict[str, int]:
    rows = fetch_rows(cfg)
    regular_rows, applied_rows = split_rows(cfg, rows)

    header: list[str] = []
    for tab in cfg.from_tabs:
        header = fetch_header_for_tab(cfg, tab)
        if header:
            break

    ensure_tab_exists(cfg, cfg.to_tab, header=header or None)
    ensure_tab_exists(cfg, cfg.applied_tab, header=header or None)

    moved_regular = append_rows(cfg, cfg.to_tab, regular_rows)
    moved_applied = append_rows(cfg, cfg.applied_tab, applied_rows)

    if rows:
        clear_tabs(cfg)

    return {
        "total": len(rows),
        "jobs": moved_regular,
        "applied": moved_applied,
    }
