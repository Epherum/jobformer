from __future__ import annotations

import csv
import datetime as dt
import os
import re
import subprocess
import sys
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Dict, List, Optional, Tuple

import typer
from rich.console import Console
from rich.live import Live
from rich.progress import BarColumn, Progress, TextColumn
from rich.table import Table


app = typer.Typer(add_completion=False)
console = Console()


DEFAULT_SHEET_ID = ""  # pass explicitly
DEFAULT_LOG = Path("data/run_log.csv")


@dataclass
class Task:
    name: str
    kind: str  # run|watch
    interval_s: int
    cmd: List[str]
    last_run_ts: Optional[float] = None
    last_exit: Optional[int] = None
    last_summary: str = ""


STAT_RE = re.compile(r"^(?P<source>\w+):\s+scraped=(?P<scraped>\d+)\s+new=(?P<new>\d+)\s+relevant_new=(?P<relevant>\d+)", re.M)
WATCH_RE = re.compile(r"NEW relevant=(?P<count>\d+)")


def _now() -> dt.datetime:
    return dt.datetime.now(dt.timezone.utc)


def _ensure_log(path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    if not path.exists():
        with path.open("w", newline="", encoding="utf-8") as f:
            w = csv.writer(f)
            w.writerow([
                "ts_utc",
                "task",
                "kind",
                "exit_code",
                "duration_s",
                "summary",
            ])


def _append_log(path: Path, row: List[str]) -> None:
    _ensure_log(path)
    with path.open("a", newline="", encoding="utf-8") as f:
        csv.writer(f).writerow(row)


def _run(cmd: List[str], timeout_s: int = 600) -> Tuple[int, str]:
    """Run command and return (exit_code, stdout+stderr)."""
    proc = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout_s)
    out = (proc.stdout or "") + ("\n" + proc.stderr if proc.stderr else "")
    return proc.returncode, out


def _parse_summary(task: Task, output: str) -> str:
    # Try to parse main stats from run.py
    m = STAT_RE.search(output)
    if m:
        return f"scraped={m.group('scraped')} new={m.group('new')} relevant_new={m.group('relevant')}"

    # Watchers
    mw = WATCH_RE.search(output)
    if mw:
        return f"new_relevant={mw.group('count')}"

    # Otherwise short fallback
    out = " ".join((output or "").strip().split())
    return out[:160]


def _task_next_run(task: Task, now_ts: float) -> float:
    if task.last_run_ts is None:
        return now_ts
    return task.last_run_ts + task.interval_s


def _build_table(tasks: List[Task], now_ts: float) -> Table:
    table = Table(title="JobScraper Dashboard", expand=True)
    table.add_column("Task", no_wrap=True)
    table.add_column("Kind", width=6)
    table.add_column("Interval")
    table.add_column("Next")
    table.add_column("Progress")
    table.add_column("Last exit", justify="right")
    table.add_column("Last summary")

    for t in tasks:
        nxt = _task_next_run(t, now_ts)
        remaining = max(0, int(nxt - now_ts))
        interval = t.interval_s

        if interval <= 0:
            prog = 1.0
        elif t.last_run_ts is None:
            prog = 0.0
        else:
            prog = min(1.0, max(0.0, (now_ts - t.last_run_ts) / interval))

        def fmt_secs(s: int) -> str:
            if s >= 3600:
                return f"{s//3600}h{(s%3600)//60:02d}"
            if s >= 60:
                return f"{s//60}m{s%60:02d}"
            return f"{s}s"

        table.add_row(
            t.name,
            t.kind,
            fmt_secs(interval),
            "now" if remaining == 0 else fmt_secs(remaining),
            f"{int(prog*100):3d}%",
            "" if t.last_exit is None else str(t.last_exit),
            t.last_summary,
        )

    return table


@app.command()
def dashboard(
    sheet_id: str = typer.Option("", help="Google Sheet ID."),
    jobs_today_tab: str = typer.Option("Jobs_Today", help="Tab where scraper appends new relevant jobs."),
    all_jobs_tab: str = typer.Option("All jobs", help="Tab name for full DB export."),
    interval_min: int = typer.Option(20, help="Full cycle interval minutes."),
    log_csv: Path = typer.Option(DEFAULT_LOG, help="CSV run log path."),
) -> None:
    """Live dashboard loop. Runs a full cycle every N minutes and sends ONE notification."""

    if not sheet_id:
        console.print("sheet_id is required (use your Jobs sheet id)")
        raise typer.Exit(2)

    # One unified cycle. Tiers are just implementation difficulty.
    sources = ["keejob", "welcometothejungle", "weworkremotely", "remoteok", "remotive", "tanitjobs", "aneti"]

    cdp = os.getenv("CDP_URL", "http://172.25.192.1:9223")

    tasks: List[Task] = [
        Task(
            name=s,
            kind="run",
            interval_s=interval_min * 60,
            cmd=[
                sys.executable,
                "-m",
                "jobscraper.run",
                "--source",
                s,
                "--once",
                "--sheet-id",
                sheet_id,
                "--sheet-tab",
                jobs_today_tab,
            ],
        )
        for s in sources
    ]

    # We'll export SQLite -> CSV and sync it to "All jobs" after each cycle.
    export_cmd = [sys.executable, "-c", "from jobscraper.export_all_jobs import export_all_jobs_csv; export_all_jobs_csv()"]

    # Loop
    with Live(_build_table(tasks, time.time()), refresh_per_second=2, console=console) as live:
        while True:
            cycle_start = time.time()
            cycle_lines: List[str] = []

            for t in tasks:
                start = time.time()
                try:
                    # For CDP-dependent sources, pass CDP_URL via env.
                    code, out = _run(t.cmd, timeout_s=900)
                except subprocess.TimeoutExpired:
                    code, out = 124, "timeout"

                dur = time.time() - start
                t.last_run_ts = time.time()
                t.last_exit = code
                t.last_summary = _parse_summary(t, out)

                _append_log(
                    log_csv,
                    [
                        _now().isoformat(timespec="seconds"),
                        t.name,
                        t.kind,
                        str(code),
                        f"{dur:.2f}",
                        t.last_summary,
                    ],
                )

                # Collect NEW lines from output (run.py prints NEW: ... | url)
                for line in (out or "").splitlines():
                    if line.startswith("NEW:"):
                        cycle_lines.append(f"{t.name}: {line[4:].strip()}")

                live.update(_build_table(tasks, time.time()))

            # Export all jobs CSV and sync it to All jobs tab.
            try:
                _run(export_cmd, timeout_s=120)
                from jobscraper.sheets_all_jobs import AllJobsSheetConfig, write_all_jobs_csv_to_sheet
                from jobscraper.export_all_jobs import ExportConfig

                csv_path = ExportConfig().out_csv
                uploaded = write_all_jobs_csv_to_sheet(
                    AllJobsSheetConfig(sheet_id=sheet_id, tab=all_jobs_tab),
                    csv_path,
                )
                _append_log(log_csv, [_now().isoformat(timespec="seconds"), "all_jobs_sync", "sync", "0", "0", f"rows={uploaded}"])
            except Exception as e:
                _append_log(log_csv, [_now().isoformat(timespec="seconds"), "all_jobs_sync", "sync", "1", "0", f"error={e}"])

            # Send ONE pushover notification if any NEW relevant lines were appended.
            if cycle_lines:
                from jobscraper.alerts.pushover import send_summary

                send_summary(
                    title=f"JobScraper: {len(cycle_lines)} new relevant",
                    lines=cycle_lines,
                    click_url=f"https://docs.google.com/spreadsheets/d/{sheet_id}",
                    click_title="Open sheet",
                )

            # sleep until next cycle
            elapsed = time.time() - cycle_start
            sleep_s = max(1, interval_min * 60 - elapsed)
            for _ in range(int(sleep_s)):
                live.update(_build_table(tasks, time.time()))
                time.sleep(1)


@app.command()
def transfer_today(
    sheet_id: str = typer.Argument(...),
    from_tab: str = typer.Option("Jobs_Today", help="Source tab (scraper output)."),
    to_tab: str = typer.Option("Jobs", help="Destination tab (your workflow + dropdown)."),
) -> None:
    """Move all rows from Jobs_Today into Jobs, then clear Jobs_Today."""
    from .transfer_today import TransferConfig, transfer_today

    n = transfer_today(TransferConfig(sheet_id=sheet_id, from_tab=from_tab, to_tab=to_tab))
    console.print(f"moved_rows={n}")


@app.command()
def run_all(sheet_id: str = typer.Argument(...), notify: bool = True) -> None:
    """Run Tier-1 sources once."""
    tier1 = ["keejob", "welcometothejungle", "weworkremotely", "remoteok", "remotive"]
    for s in tier1:
        cmd = [sys.executable, "-m", "jobscraper.run", "--source", s, "--once", "--sheet-id", sheet_id]
        if notify:
            cmd.append("--notify")
        code, out = _run(cmd)
        console.print(f"{s}: exit={code}")
        console.print(_parse_summary(Task(s, 'run', 0, []), out))


if __name__ == "__main__":
    app()
