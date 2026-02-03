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


SUSPICIOUS_ZERO_SCRAPE = {
    # These sources almost always return >0 scraped when healthy.
    # If they return 0, it's often a parsing/layout change, blocking, or network issue.
    "keejob",
    "welcometothejungle",
    "weworkremotely",
    "remoteok",
    "tanitjobs",
    "aneti",
    "linkedin",
}


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


def _detect_issues(task: Task, code: int, output: str) -> list[str]:
    issues: list[str] = []

    if code != 0:
        issues.append(f"{task.name}: exit={code}")

    m = STAT_RE.search(output or "")
    if m:
        scraped = int(m.group("scraped"))
        if scraped == 0 and task.name in SUSPICIOUS_ZERO_SCRAPE:
            issues.append(f"{task.name}: scraped=0 (blocked/layout change/CDP not ready)")

    # Common transient failure hints
    o = (output or "")
    if "429" in o or "Too Many Requests" in o:
        issues.append(f"{task.name}: rate-limited (429)")
    if "403" in o and task.name in {"tanitjobs", "aneti"}:
        issues.append(f"{task.name}: forbidden/blocked (403)")
    if "Web Page Blocked" in o:
        issues.append(f"{task.name}: blocked")

    # De-dupe
    out: list[str] = []
    seen = set()
    for it in issues:
        if it in seen:
            continue
        seen.add(it)
        out.append(it)
    return out


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
def doctor() -> None:
    """Best-effort environment check for day-to-day reliability."""
    from .config import load_config
    from .smoke import smoke_checks

    cfg = load_config()
    results = smoke_checks(cfg)

    bad = 0
    for r in results:
        status = "OK" if r.ok else "FAIL"
        console.print(f"{status} {r.name}: {r.detail}")
        if not r.ok:
            bad += 1

    raise typer.Exit(1 if bad else 0)


@app.command()
def dashboard(
    sheet_id: str = typer.Option("", help="Google Sheet ID (or set SHEET_ID in data/config.env)."),
    jobs_today_tab: str = typer.Option("", help="Tab where scraper appends new relevant jobs."),
    all_jobs_tab: str = typer.Option("", help="Tab name for full DB export."),
    interval_min: int = typer.Option(0, help="Full cycle interval minutes."),
    log_csv: Path = typer.Option(DEFAULT_LOG, help="CSV run log path."),
) -> None:
    """Live dashboard loop.

    Behavior:
    - Prints the exact Windows command to start Chrome in CDP mode + open the required sites.
    - Runs the smoke test automatically.
    - If smoke fails, exits before starting the dashboard loop.
    """

    from .config import AppConfig, load_config
    from .smoke import smoke_checks

    cfg = load_config()

    # Resolve effective config (CLI overrides > env/config file).
    sheet_id = sheet_id or cfg.sheet_id
    jobs_today_tab = jobs_today_tab or cfg.jobs_today_tab
    all_jobs_tab = all_jobs_tab or cfg.all_jobs_tab
    interval_min = interval_min or cfg.interval_min

    effective_cfg = AppConfig(
        sheet_id=sheet_id,
        sheet_account=cfg.sheet_account,
        jobs_tab=cfg.jobs_tab,
        jobs_today_tab=jobs_today_tab,
        all_jobs_tab=all_jobs_tab,
        cdp_url=cfg.cdp_url,
        interval_min=interval_min,
    )

    # Always print the Windows snippet first so you can quickly start CDP Chrome.
    tanit_url = "https://www.tanitjobs.com/jobs/"
    aneti_url = "https://www.emploi.nat.tn/fo/Fr/global.php?page=146&=true&FormLinks_Sorting=7&FormLinks_Sorted=7"
    console.print("\nWindows (PowerShell) snippet to start Chrome in CDP mode and open the 2 sites:")
    console.print(
        """
# Pick chrome.exe (adjust if needed)
$Chrome = "$env:ProgramFiles\\Google\\Chrome\\Application\\chrome.exe"
if (!(Test-Path $Chrome)) { $Chrome = "$env:ProgramFiles(x86)\\Google\\Chrome\\Application\\chrome.exe" }

# Separate profile so it doesn't fight with your normal Chrome
$UserData = "$env:LOCALAPPDATA\\JobScraperChrome"

Start-Process $Chrome -ArgumentList @(
  "--remote-debugging-port=9223",
  "--user-data-dir=$UserData",
  """ + tanit_url + """,
  """ + aneti_url + """
)
""".strip()
    )
    console.print(f"Expected CDP URL from WSL: {effective_cfg.cdp_url} (should respond at /json/version)\n")

    # Auto smoke test.
    results = smoke_checks(effective_cfg)
    bad = 0
    for r in results:
        status = "OK" if r.ok else "FAIL"
        console.print(f"{status} {r.name}: {r.detail}")
        if not r.ok:
            bad += 1

    if bad:
        raise typer.Exit(1)

    if not sheet_id:
        console.print("sheet_id is required (pass --sheet-id or set SHEET_ID in data/config.env)")
        raise typer.Exit(2)

    # One unified cycle. Tiers are just implementation difficulty.
    # Tier-1 sources are server-side; Tier-2 are CDP-based (Windows Chrome).
    sources = ["keejob", "welcometothejungle", "weworkremotely", "remoteok", "remotive", "tanitjobs", "aneti", "linkedin"]

    cdp = cfg.cdp_url

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
            cycle_issues: List[str] = []

            for t in tasks:
                start = time.time()
                try:
                    # Pass CDP_URL explicitly so subcommands always see the right value.
                    env = {**os.environ, "CDP_URL": cdp}
                    proc = subprocess.run(t.cmd, capture_output=True, text=True, timeout=900, env=env)
                    code = proc.returncode
                    out = (proc.stdout or "") + ("\n" + proc.stderr if proc.stderr else "")
                except subprocess.TimeoutExpired:
                    code, out = 124, "timeout"

                dur = time.time() - start
                # Important: we run a full cycle (all sources), then start ONE timer.
                # So we do NOT stamp per-task last_run_ts here.
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

                # Collect issues (best-effort mode: keep running, but surface problems).
                for iss in _detect_issues(t, code, out):
                    cycle_issues.append(iss)

                # Collect NEW lines from output (run.py prints NEW: ... | url)
                for line in (out or "").splitlines():
                    if line.startswith("NEW:"):
                        payload = line[4:].strip()
                        # run.py prints e.g.:
                        # - "NEW: title | url"
                        # - "NEW: title | company | location | url"
                        # We only want the title to keep notifications short.
                        title_only = payload.split(" | ", 1)[0].strip()
                        if title_only:
                            cycle_lines.append(title_only)

                # Update view after each task.
                live.update(_build_table(tasks, time.time()))

            # Mark the cycle end. Align all task timers to this single point.
            cycle_end = time.time()
            for t in tasks:
                t.last_run_ts = cycle_end

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

            # Send ONE pushover notification per cycle.
            # - If we have new relevant jobs: send titles only.
            # - If we have issues (best-effort mode): include a short issues section.
            if cycle_lines or cycle_issues:
                from jobscraper.alerts.pushover import send_summary

                lines: List[str] = []
                lines.extend(cycle_lines)

                # Add issues at the end to keep the top of the notification useful.
                if cycle_issues:
                    lines.append("---")
                    lines.append("Issues:")
                    # de-dupe and cap
                    uniq = []
                    seen = set()
                    for it in cycle_issues:
                        if it in seen:
                            continue
                        seen.add(it)
                        uniq.append(it)
                    lines.extend(uniq[:8])
                    if len(uniq) > 8:
                        lines.append("…")

                title = f"JobScraper: {len(cycle_lines)} new"
                if cycle_issues:
                    title += f" | {len(set(cycle_issues))} issues"

                send_summary(title=title, lines=lines)

            # sleep until next cycle
            elapsed = time.time() - cycle_start
            sleep_s = max(1, interval_min * 60 - elapsed)
            for _ in range(int(sleep_s)):
                live.update(_build_table(tasks, time.time()))
                time.sleep(1)


@app.command()
def smoke() -> None:
    """Quick dependency check: SQLite, CDP, Pushover config, Sheets access."""
    from .config import load_config
    from .smoke import smoke_checks

    cfg = load_config()
    results = smoke_checks(cfg)

    bad = 0
    for r in results:
        status = "OK" if r.ok else "FAIL"
        console.print(f"{status} {r.name}: {r.detail}")
        if not r.ok:
            bad += 1

    raise typer.Exit(1 if bad else 0)


@app.command(name="linkedin-first-page")
def linkedin_first_page(
    url: str = typer.Argument(..., help="LinkedIn jobs search URL (scrapes first page only)."),
    out_json: Path = typer.Option(Path("data/linkedin_first_page.json"), help="Output JSON path."),
    timeout_ms: int = typer.Option(30_000, help="Timeout in milliseconds."),
) -> None:
    """Scrape the first page of a LinkedIn jobs search via the existing CDP Chrome session."""
    from .config import load_config
    from .linkedin_first_page_cdp import LinkedInFirstPageConfig, scrape_first_page_via_cdp

    cfg = load_config()
    payload = scrape_first_page_via_cdp(
        cfg,
        LinkedInFirstPageConfig(url=url, timeout_ms=timeout_ms, out_json=out_json),
    )

    console.print(f"OK linkedin-first-page: count={payload.get('count', 0)} out={out_json}")


@app.command()
def transfer_today(
    sheet_id: str = typer.Argument("", help="Google Sheet ID (or set SHEET_ID in data/config.env)."),
    from_tab: str = typer.Option("", help="Source tab (scraper output)."),
    to_tab: str = typer.Option("", help="Destination tab (your workflow + dropdown)."),
) -> None:
    """Move all rows from Jobs_Today into Jobs, then clear Jobs_Today."""
    from .config import load_config
    from .transfer_today import TransferConfig, transfer_today

    cfg = load_config()
    sheet_id = sheet_id or cfg.sheet_id
    from_tab = from_tab or cfg.jobs_today_tab
    to_tab = to_tab or cfg.jobs_tab

    if not sheet_id:
        console.print("sheet_id is required (pass as arg or set SHEET_ID in data/config.env)")
        raise typer.Exit(2)

    n = transfer_today(TransferConfig(sheet_id=sheet_id, from_tab=from_tab, to_tab=to_tab, account=cfg.sheet_account))
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
