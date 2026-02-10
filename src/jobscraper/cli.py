from __future__ import annotations

import csv
import datetime as dt
import os
import re
import subprocess
import sys
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Dict, List, Optional, Tuple

import typer
from rich.align import Align
from rich.console import Console
from rich.live import Live
from rich.layout import Layout
from rich.panel import Panel
from rich.progress import BarColumn, Progress, TextColumn
from rich.table import Table
from rich.text import Text
from rich.console import Group


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

# UI parsing helpers (compact summaries)
STAT_SUMMARY_RE = re.compile(r"scraped=(?P<scraped>\d+)\s+new=(?P<new>\d+)\s+relevant_new=(?P<relevant>\d+)")
EXTRACT_SUMMARY_RE = re.compile(r"candidates=(?P<cand>\d+)\s+ok=(?P<ok>\d+)\s+blocked=(?P<blocked>\d+)")
SCORE_SUMMARY_RE = re.compile(r"passes<=\d+\s+scored=(?P<scored>\d+)\s+updated=(?P<updated>\d+)\s+errors=(?P<errors>\d+)")
SCORE_PASS_RE = re.compile(r"pass=\d+/\d+\s+scored=(?P<scored>\d+)\s+updated=(?P<updated>\d+)\s+missing=(?P<missing>\d+)")


SUSPICIOUS_ZERO_SCRAPE = {
    # These sources almost always return >0 scraped when healthy.
    # If they return 0, it's often a parsing/layout change, blocking, or network issue.
    "keejob",
    "welcometothejungle",
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


def _parse_summary(task: Task, output: str, exit_code: Optional[int] = None) -> str:
    # Try to parse main stats from run.py
    m = STAT_RE.search(output)
    if m:
        return f"scraped={m.group('scraped')} new={m.group('new')} relevant_new={m.group('relevant')}"

    # Watchers
    mw = WATCH_RE.search(output)
    if mw:
        return f"new_relevant={mw.group('count')}"

    lines = [ln.strip() for ln in (output or "").splitlines() if ln.strip()]
    if lines:
        cleaned = [ln for ln in lines if "DeprecationWarning" not in ln]
        if not cleaned:
            cleaned = lines
        if exit_code and exit_code != 0:
            return cleaned[-1][:160]

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
    if "connect_over_cdp" in o or "CDP" in o and "Timeout" in o:
        issues.append(f"{task.name}: CDP connect timeout/busy")
    if "ECONNREFUSED" in o and "922" in o:
        issues.append(f"{task.name}: CDP refused (Chrome not running?)")

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


@dataclass
class DashboardState:
    phase: str = "starting"
    cycle_no: int = 0
    started_ts: float = 0.0
    new_relevant: int = 0
    issues: int = 0
    unscored_remaining: Optional[int] = None
    cache_ok: int = 0
    cache_blocked: int = 0
    sources_done: int = 0
    sources_total: int = 0
    extract_processed: int = 0
    extract_total: int = 0
    score_scored: int = 0
    score_target: int = 0
    last_results: List[Tuple[str, str, str]] = field(default_factory=list)


def _shorten(text: str, max_len: int = 64) -> str:
    t = (text or "").strip()
    if len(t) <= max_len:
        return t
    return t[: max(0, max_len - 1)].rstrip() + "…"


def _fmt_secs(s: int) -> str:
    if s >= 3600:
        return f"{s//3600}h{(s%3600)//60:02d}"
    if s >= 60:
        return f"{s//60}m{s%60:02d}"
    return f"{s}s"


def _color_num(n: int, *, good_when_zero: bool = False) -> Text:
    if good_when_zero and n == 0:
        return Text(str(n), style="green")
    if n == 0:
        return Text(str(n), style="dim")
    return Text(str(n), style="bold")


def _format_recent_summary(summary: str) -> Text:
    """Turn verbose summaries into compact, readable, color-coded text."""

    s = (summary or "").strip()

    m = STAT_SUMMARY_RE.search(s)
    if m:
        scraped = int(m.group("scraped"))
        new = int(m.group("new"))
        rel = int(m.group("relevant"))
        out = Text()
        out.append_text(_color_num(scraped))
        out.append(" ")
        out.append_text(_color_num(new))
        out.append(" ")
        out.append_text(_color_num(rel))
        return out

    m = EXTRACT_SUMMARY_RE.search(s)
    if m:
        cand = int(m.group("cand"))
        ok = int(m.group("ok"))
        blocked = int(m.group("blocked"))
        out = Text()
        out.append_text(_color_num(ok))
        out.append("/")
        out.append_text(_color_num(cand))
        out.append("  ")
        out.append("blk=")
        out.append_text(_color_num(blocked, good_when_zero=True))
        return out

    m = SCORE_PASS_RE.search(s) or SCORE_SUMMARY_RE.search(s)
    if m:
        # For pass lines: scored/updated/missing. For final: scored/updated/errors.
        out = Text()
        scored = int(m.group("scored"))
        updated = int(m.group("updated"))
        out.append("sc=")
        out.append_text(_color_num(scored))
        out.append(" up=")
        out.append_text(_color_num(updated))
        if "missing" in m.groupdict():
            missing = int(m.group("missing"))
            out.append(" miss=")
            out.append_text(_color_num(missing, good_when_zero=True))
        elif "errors" in m.groupdict():
            errors = int(m.group("errors"))
            out.append(" err=")
            out.append_text(_color_num(errors, good_when_zero=True))
        return out

    return Text(_shorten(s, 80))


def _init_dashboard_layout(progress: Progress) -> Layout:
    """Create a stable Rich layout.

    Important for flicker reduction: keep the Progress instance stable across updates.
    """
    layout = Layout()
    layout.split_column(
        Layout(name="header", size=5),
        Layout(name="body"),
        Layout(name="footer", size=3),
    )
    layout["body"].split_row(Layout(name="left"), Layout(name="right"))

    # Small static ASCII decal, kept stable to avoid any layout reflow/flicker.
    # Keep it static (no animation) and fixed-height to avoid reflow.
    # Compact logo to avoid cropping in narrower terminals.
    ascii_logo = (
        "     _       _      __                              \n"
        "    (_) ___ | |__  / _| ___  _ __ _ __ ___   ___ _ __\n"
        "    | |/ _ \\| '_ \\| |_ / _ \\| '__| '_ ` _ \\ / _ \\ '__|\n"
        "    | | (_) | |_) |  _| (_) | |  | | | | | |  __/ |   \n"
        "   _/ |\\___/|_.__/|_|  \\___/|_|  |_| |_| |_|\\___|_|   \n"
        "  |__/   jobformer\n"
    )

    # Keep ASCII stable and avoid wrapping, which makes it look "messed up".
    decal = Text(ascii_logo, justify="center", no_wrap=True, overflow="crop", style="bold")

    legend = Text(justify="center")
    legend.append("Legend: ", style="dim")
    legend.append("scraped/new/relevant", style="dim")
    legend.append("  •  ", style="dim")
    legend.append("ok/total blk=0", style="dim")

    left_group = Group(
        progress,
        Text(""),
        Panel(decal, border_style="dim", padding=(0, 2)),
        Panel(legend, border_style="dim", padding=(0, 1)),
    )

    layout["left"].update(Panel(left_group, title="Progress", padding=(1, 1)))
    layout["right"].update(Panel(Text(""), title="Recent results", padding=(0, 1)))
    layout["header"].update(Panel(Text(""), title="JobScraper", padding=(0, 1)))
    layout["footer"].update(Panel(Text(""), padding=(0, 1)))
    return layout


def _refresh_dashboard_layout(layout: Layout, tasks: List[Task], now_ts: float, state: DashboardState) -> None:
    """Update header/right/footer panels. Progress is updated separately."""

    header = Table.grid(expand=True)
    header.add_column(ratio=2)
    header.add_column(ratio=1)
    header.add_column(ratio=1)
    header.add_column(ratio=1)
    header.add_row(
        f"[bold]Phase:[/bold] {state.phase}",
        f"[bold]New relevant:[/bold] {state.new_relevant}",
        f"[bold]Issues:[/bold] {state.issues}",
        f"[bold]Unscored:[/bold] {state.unscored_remaining if state.unscored_remaining is not None else '-'}",
    )
    header.add_row(
        f"[bold]Cache ok/blocked:[/bold] {state.cache_ok}/{state.cache_blocked}",
        f"[bold]Cycle:[/bold] {state.cycle_no}",
        f"[bold]Uptime:[/bold] {_fmt_secs(int(max(0, now_ts - (state.started_ts or now_ts))))}",
        "",
    )
    layout["header"].update(Panel(header, title="JobScraper", padding=(0, 1)))

    recent = Table(expand=True, show_header=True)
    recent.add_column("Task", no_wrap=True)
    recent.add_column("Exit", width=6, justify="right")
    recent.add_column("Summary")
    for name, exit_code, summary in (state.last_results or [])[-8:]:
        code_txt = (exit_code or "").strip() or "-"
        if code_txt == "0":
            code_cell = Text(code_txt, style="green")
        elif code_txt in {"-", ""}:
            code_cell = Text(code_txt, style="dim")
        else:
            code_cell = Text(code_txt, style="red")
        recent.add_row(name, code_cell, _format_recent_summary(summary))
    layout["right"].update(Panel(recent, title="Recent results", padding=(0, 1)))

    footer = Text()
    footer.append("Phase: ", style="dim")
    footer.append(state.phase, style="bold")

    footer.append("  •  ")
    footer.append("New: ", style="dim")
    footer.append(str(state.new_relevant))

    footer.append("  •  ")
    footer.append("Issues: ", style="dim")
    footer.append(str(state.issues))

    footer.append("  •  ")
    footer.append("Cache ok/blocked: ", style="dim")
    footer.append(f"{state.cache_ok}/{state.cache_blocked}")

    if state.unscored_remaining is not None:
        footer.append("  •  ")
        footer.append("Unscored: ", style="dim")
        footer.append(str(state.unscored_remaining))

    footer.append("  •  ")
    footer.append(f"Tasks: {len(tasks)}", style="dim")

    layout["footer"].update(Panel(Align.left(footer), padding=(0, 1)))


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
    show_windows_snippet: bool = typer.Option(False, help="Print the Windows PowerShell snippet for starting Chrome in CDP mode."),
    once: bool = typer.Option(False, "--once/--no-once", help="Run one cycle and exit."),
) -> None:
    """Live dashboard loop.

    Behavior:
    - Runs the smoke test automatically.
    - If smoke fails, exits before starting the dashboard loop.

    Tip:
    - Pass --show-windows-snippet if you need the PowerShell helper to start CDP Chrome.
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

    if show_windows_snippet:
        # Optional helper snippet to quickly start CDP Chrome from Windows.
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
  "--remote-debugging-port=9224",
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
    sources = ["keejob", "welcometothejungle", "remoteok", "tanitjobs", "aneti"]

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

    # Extra transparency rows in the dashboard.
    # all_jobs_sync removed from automatic dashboard pipeline.
    notify_task = Task(
        name="notify",
        kind="notify",
        interval_s=interval_min * 60,
        cmd=[],
        last_summary="pending",
    )
    extract_task = Task(
        name="extract_text",
        kind="extract",
        interval_s=interval_min * 60,
        cmd=[],
        last_summary="pending",
    )
    score_task = Task(
        name="llm_score",
        kind="score",
        interval_s=interval_min * 60,
        cmd=[],
        last_summary="pending",
    )

    # LinkedIn: render as separate dashboard rows (TN/FR/GR) instead of one combined row.
    # We infer the URL -> label from geoId.
    def _parse_linkedin_urls() -> dict[str, str]:
        raw = (os.getenv("LINKEDIN_URLS") or "").strip()
        if raw:
            parts = [p.strip() for p in raw.split(",") if p.strip()]
        else:
            single = (os.getenv("LINKEDIN_URL") or "").strip()
            parts = [single] if single else []

        out: dict[str, str] = {}
        for u in parts:
            if "geoId=102134353" in u:
                out.setdefault("TN", u)
            elif "geoId=105015875" in u:
                out.setdefault("FR", u)
            elif "geoId=101282230" in u:
                # User asked for GR label (Germany).
                out.setdefault("GR", u)
            else:
                out.setdefault("LI", u)
        return out

    li = _parse_linkedin_urls()
    for label in ["TN", "FR", "GR", "LI"]:
        if label not in li:
            continue
        tasks.append(
            Task(
                name=f"linkedin {label}",
                kind="run",
                interval_s=interval_min * 60,
                cmd=[
                    sys.executable,
                    "-m",
                    "jobscraper.run",
                    "--source",
                    "linkedin",
                    "--once",
                    "--linkedin-url",
                    li[label],
                    "--sheet-id",
                    sheet_id,
                    "--sheet-tab",
                    jobs_today_tab,
                ],
            )
        )

    # We'll export SQLite -> CSV and sync it to "All jobs" after each cycle.
    # NOTE: pushing the full DB to the "All jobs" sheet is now manual (see push-all-jobs).

    disable_score = (os.getenv("DISABLE_LLM_SCORE") or "").strip().lower() in {"1", "true", "yes", "y"}

    dashboard_rows = tasks + [extract_task, score_task, notify_task]
    state = DashboardState(sources_total=len(tasks), started_ts=time.time())
    is_tty = console.is_terminal and sys.stdout.isatty()

    def _add_result(name: str, exit_code: Optional[int], summary: str) -> None:
        state.last_results.append((name, "" if exit_code is None else str(exit_code), summary))

    # Stable Progress instance (reduces flicker vs recreating Progress every update)
    progress = Progress(
        TextColumn("[bold]{task.description}[/bold]"),
        BarColumn(bar_width=None),
        TextColumn("{task.completed}/{task.total}"),
        expand=True,
    )
    sources_task_id = progress.add_task("Sources", completed=0, total=max(state.sources_total, 1))
    extract_task_id = progress.add_task("Extract text", completed=0, total=1)
    score_task_id = progress.add_task("Score cache", completed=0, total=1)

    layout = _init_dashboard_layout(progress)

    def _sync_progress_from_state() -> None:
        progress.update(sources_task_id, completed=state.sources_done, total=max(state.sources_total, 1))
        progress.update(
            extract_task_id,
            completed=state.extract_processed,
            total=state.extract_total if state.extract_total > 0 else 1,
        )
        progress.update(
            score_task_id,
            completed=state.score_scored,
            total=state.score_target if state.score_target > 0 else 1,
        )

    last_ui_update_ts: float = 0.0

    def _update_live(live: Optional[Live] = None, *, force: bool = False) -> None:
        """Update the Rich Live UI.

        Key flicker fix: keep the same Layout + Progress instances. Only update panel contents.
        """
        nonlocal last_ui_update_ts
        if not is_tty or live is None:
            return
        now = time.time()
        if not force and (now - last_ui_update_ts) < 0.6:
            return
        last_ui_update_ts = now
        _sync_progress_from_state()
        _refresh_dashboard_layout(layout, dashboard_rows, now, state)
        # Avoid swapping the root renderable; just refresh.
        live.refresh()

    def _plain_print(line: str) -> None:
        if is_tty:
            return
        console.print(line)

    # Using screen=True tends to reduce flicker in many terminals.
    live_ctx = Live(layout, refresh_per_second=4, console=console, screen=True, transient=False) if is_tty else None

    # Loop
    if live_ctx:
        live_ctx.__enter__()
    try:
        while True:
            cycle_start = time.time()
            cycle_lines = []
            cycle_issues = []
            state.cycle_no += 1
            state.new_relevant = 0
            state.issues = 0
            state.unscored_remaining = None
            state.cache_ok = 0
            state.cache_blocked = 0
            state.sources_done = 0
            state.extract_processed = 0
            state.extract_total = 0
            state.score_scored = 0
            state.score_target = 0

            state.phase = "Scrape sources"
            _update_live(live_ctx, force=True)

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
                t.last_summary = _parse_summary(t, out, exit_code=code)

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

                state.sources_done += 1
                state.phase = f"Scrape sources ({state.sources_done}/{state.sources_total})"
                _add_result(t.name, code, t.last_summary)
                _plain_print(f"{t.name}: exit={code} summary={_shorten(t.last_summary)}")
                _update_live(live_ctx)

            # Mark the cycle end. Align all task timers to this single point.
            cycle_end = time.time()
            for t in tasks:
                t.last_run_ts = cycle_end

            # Extract job text into cache.
            state.phase = "Extract text (cache)"
            extract_task.last_exit = None
            extract_task.last_summary = "extracting text"
            _update_live(live_ctx)
            try:
                from jobscraper.sheets_sync import SheetsConfig, _get_sheet_rows
                from jobscraper.text_extraction import extract_text_for_urls

                sheet_cfg = SheetsConfig(sheet_id=sheet_id, tab=jobs_today_tab, account=cfg.sheet_account)
                rows = _get_sheet_rows(sheet_cfg)

                # Collect URLs from Jobs_Today that still need scoring.
                urls: list[str] = []
                for r in reversed((rows or [])[1:]):
                    if len(r) < 7:
                        continue
                    url = (r[6] or "").strip()
                    score = (r[8] or "").strip() if len(r) > 8 else ""
                    if not url or score:
                        continue

                    # Tanitjobs detail pages frequently trigger Cloudflare challenges.
                    # Keep Tanitjobs for scraping job *listings*, but skip detail text extraction in the dashboard pipeline.
                    if "tanitjobs.com" in url:
                        continue

                    urls.append(url)

                max_fetch = int((os.getenv("TEXT_FETCH_MAX_JOBS") or "50").strip() or "50")
                state.extract_total = min(len(urls), max_fetch) if max_fetch else len(urls)
                state.extract_processed = 0
                state.cache_ok = 0
                state.cache_blocked = 0
                _update_live(live_ctx, force=True)

                def _on_extract(res, stats) -> None:
                    # stats contains running totals
                    state.extract_total = int(stats.get("candidates", 0) or 0) or state.extract_total
                    state.extract_processed = int(stats.get("fetched", 0) or 0)
                    state.cache_ok = int(stats.get("ok", 0) or 0)
                    state.cache_blocked = int(stats.get("blocked", 0) or 0)
                    _update_live(live_ctx)

                summary = extract_text_for_urls(
                    urls=urls,
                    db_path=str(Path("data") / "jobs.sqlite3"),
                    max_jobs=max_fetch,
                    refresh=False,
                    progress_cb=_on_extract,
                )

                extract_task.last_exit = 0
                extract_task.last_summary = f"candidates={summary['candidates']} ok={summary['ok']} blocked={summary['blocked']}"
                state.extract_total = int(summary.get("candidates", 0) or 0)
                state.extract_processed = int(summary.get("fetched", 0) or 0)
                state.cache_ok = int(summary.get("ok", 0) or 0)
                state.cache_blocked = int(summary.get("blocked", 0) or 0)
            except Exception as e:
                extract_task.last_exit = 1
                extract_task.last_summary = f"error={e}"[:160]
            _add_result(extract_task.name, extract_task.last_exit, extract_task.last_summary)
            _plain_print(f"extract_text: {extract_task.last_summary}")
            _update_live(live_ctx)

            # LLM scoring from cached text.
            # Run up to 3 passes if there are still unscored rows.
            state.phase = "Score (cached) pass 1/3"
            score_task.last_exit = None
            score_task.last_summary = "scoring"
            _update_live(live_ctx)
            if disable_score:
                score_task.last_exit = 0
                score_task.last_summary = "skipped (DISABLE_LLM_SCORE=1)"
            else:
                try:
                    from jobscraper.job_scoring_cached import score_unscored_sheet_rows_from_cache
                    from jobscraper.llm_score import DEFAULT_MODEL
                    from jobscraper.sheets_sync import SheetsConfig

                    model = (os.getenv("LLM_MODEL") or "").strip() or DEFAULT_MODEL
                    max_jobs = int((os.getenv("TEXT_FETCH_MAX_JOBS") or "50").strip() or "50")

                    total_scored = 0
                    total_updated = 0
                    total_errors = 0
                    last_missing = None

                    for p in range(1, 4):
                        state.phase = f"Score (cached) pass {p}/3"
                        _update_live(live_ctx)

                        state.score_scored = 0
                        state.score_target = max_jobs
                        _update_live(live_ctx, force=True)

                        def _on_score(ev: dict) -> None:
                            # ev: {kind, url, processed, total}
                            state.score_scored = int(ev.get("processed", 0) or 0)
                            state.score_target = int(ev.get("total", 0) or 0) or max_jobs
                            _update_live(live_ctx)

                        summary = score_unscored_sheet_rows_from_cache(
                            db_path=Path("data") / "jobs.sqlite3",
                            model=model,
                            sheet_cfg=SheetsConfig(sheet_id=sheet_id, tab=jobs_today_tab, account=cfg.sheet_account),
                            max_jobs=max_jobs,
                            concurrency=2,
                            extract_missing=False,
                            progress_cb=_on_score,
                        )

                        total_scored += int(summary.get("scored", 0) or 0)
                        total_updated += int(summary.get("updated_rows", 0) or 0)
                        total_errors += int(summary.get("errors", 0) or 0)

                        missing = int(summary.get("missing", 0) or 0)
                        state.unscored_remaining = missing
                        # Keep the progress bar as "processed/total" instead of "scored/max".
                        # Mark the pass as finished.
                        state.score_scored = state.score_target

                        score_task.last_summary = f"pass={p}/3 scored={summary['scored']} updated={summary['updated_rows']} missing={missing}"
                        _update_live(live_ctx)

                        # Stop early if no missing remain, or if missing is not changing.
                        if missing == 0:
                            break
                        if last_missing is not None and missing == last_missing:
                            break
                        last_missing = missing

                    score_task.last_exit = 0
                    score_task.last_summary = f"passes<=3 scored={total_scored} updated={total_updated} errors={total_errors}"
                except Exception as e:
                    score_task.last_exit = 1
                    score_task.last_summary = f"error={e}"[:160]

            _add_result(score_task.name, score_task.last_exit, score_task.last_summary)
            _plain_print(f"score_cached: {score_task.last_summary}")
            _update_live(live_ctx)

            # All-jobs sheet sync is now a manual command (push-all-jobs).

            # Send ONE pushover notification per cycle.
            # - If we have new relevant jobs: send titles only.
            # - If we have issues (best-effort mode): include a short issues section.
            state.phase = "Notify"
            notify_task.last_exit = 0
            notify_task.last_summary = "no notification"
            if cycle_lines or cycle_issues:
                notify_task.last_summary = "sending pushover"
                _update_live(live_ctx)

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

                try:
                    send_summary(title=title, lines=lines)
                    notify_task.last_exit = 0
                    notify_task.last_summary = f"sent ({len(cycle_lines)} new)"
                except Exception as e:
                    notify_task.last_exit = 1
                    notify_task.last_summary = f"error={e}"[:160]
            _add_result(notify_task.name, notify_task.last_exit, notify_task.last_summary)
            _plain_print(f"notify: {notify_task.last_summary}")
            _update_live(live_ctx)

            state.new_relevant = len(cycle_lines)
            state.issues = len(set(cycle_issues))

            # sleep until next cycle
            elapsed = time.time() - cycle_start
            if once:
                break

            sleep_s = max(1, interval_min * 60 - elapsed)
            for sec in range(int(sleep_s)):
                state.phase = f"Sleep ({_fmt_secs(int(sleep_s - sec))})"
                # Avoid flicker: update UI at most every ~5 seconds during long sleeps.
                if sec % 5 == 0:
                    _update_live(live_ctx)
                time.sleep(1)
    finally:
        if live_ctx:
            live_ctx.__exit__(None, None, None)


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


@app.command(name="score-today")
def score_today(
    sheet_id: str = typer.Option("", help="Google Sheet ID (or set SHEET_ID in data/config.env)."),
    sheet_tab: str = typer.Option("", help="Sheet tab to update (default Jobs_Today)."),
    since_hours: int = typer.Option(24, help="Lookback window in hours."),
    max_jobs: int = typer.Option(50, help="Maximum jobs to score in one run."),
    concurrency: int = typer.Option(2, help="Scoring concurrency."),
    model: str = typer.Option("", help="Ollama model (default qwen2.5:7b-instruct)."),
    update_sheet: bool = typer.Option(True, "--update-sheet/--no-update-sheet", help="Update Jobs_Today with score columns (I:J)."),
) -> None:
    """Score recent relevant jobs from the DB and optionally update Jobs_Today (I:J)."""
    from .config import load_config
    from .job_scoring import score_recent_jobs
    from .job_scoring_sheet import score_unscored_sheet_rows
    from .llm_score import DEFAULT_MODEL
    from .sheets_sync import SheetsConfig

    cfg = load_config()
    sheet_id = sheet_id or cfg.sheet_id
    sheet_tab = sheet_tab or cfg.jobs_today_tab

    if update_sheet and not sheet_id:
        console.print("sheet_id is required when --update-sheet is set")
        raise typer.Exit(2)

    end_ts = time.time()
    start_ts = end_ts - (since_hours * 3600)

    sheet_cfg = None
    if update_sheet and sheet_id:
        sheet_cfg = SheetsConfig(sheet_id=sheet_id, tab=sheet_tab, account=cfg.sheet_account)

    if update_sheet and sheet_cfg is not None:
        # Score the sheet rows directly so results show up in Jobs_Today.
        summary = score_unscored_sheet_rows(
            db_path=Path("data") / "jobs.sqlite3",
            model=model.strip() or DEFAULT_MODEL,
            sheet_cfg=sheet_cfg,
            max_jobs=max_jobs,
            concurrency=1,
        )

        console.print(
            f"scored={summary['scored']} updated_rows={summary['updated_rows']} candidates={summary['candidates']} errors={summary['errors']}"
        )
    else:
        summary = score_recent_jobs(
            db_path=Path("data") / "jobs.sqlite3",
            start_ts=start_ts,
            end_ts=end_ts,
            model=model.strip() or DEFAULT_MODEL,
            sheet_cfg=sheet_cfg,
            update_sheet=update_sheet,
            max_jobs=max_jobs,
            concurrency=concurrency,
        )

        console.print(
            f"scored={summary['scored']} updated_rows={summary['updated_rows']} filtered={summary['filtered']} "
            f"errors={summary['errors']} linkedin_skipped={summary.get('linkedin_skipped', 0)}"
        )


@app.command(name="extract-text")
def extract_text(
    sheet_id: str = typer.Option("", help="Google Sheet ID (or set SHEET_ID in data/config.env)."),
    sheet_tab: str = typer.Option("", help="Sheet tab to read (default Jobs_Today)."),
    max_jobs: int = typer.Option(0, help="Maximum jobs to fetch in one run (0=env/default)."),
    refresh: bool = typer.Option(False, help="Force refresh even if cached text exists."),
    verbose: bool = typer.Option(False, help="Print a short per-URL result line."),
) -> None:
    """Extract job page text into SQLite cache (job_text_cache)."""
    from .config import load_config
    from .text_extraction import extract_text_for_sheet
    from .sheets_sync import SheetsConfig

    cfg = load_config()
    sheet_id = sheet_id or cfg.sheet_id
    sheet_tab = sheet_tab or cfg.jobs_today_tab

    if not sheet_id:
        console.print("sheet_id is required")
        raise typer.Exit(2)

    effective_max = max_jobs if max_jobs > 0 else None

    summary = extract_text_for_sheet(
        sheet_cfg=SheetsConfig(sheet_id=sheet_id, tab=sheet_tab, account=cfg.sheet_account),
        db_path=str(Path("data") / "jobs.sqlite3"),
        max_jobs=effective_max,
        refresh=refresh,
        verbose=verbose,
    )

    console.print(
        f"candidates={summary['candidates']} fetched={summary['fetched']} ok={summary['ok']} "
        f"blocked={summary['blocked']} empty={summary['empty']} errors={summary['errors']}"
    )


@app.command(name="score-cached")
def score_cached(
    sheet_id: str = typer.Option("", help="Google Sheet ID (or set SHEET_ID in data/config.env)."),
    sheet_tab: str = typer.Option("", help="Sheet tab to update (default Jobs_Today)."),
    max_jobs: int = typer.Option(50, help="Maximum jobs to score in one run."),
    concurrency: int = typer.Option(2, help="Scoring concurrency."),
    model: str = typer.Option("", help="Ollama model (default qwen2.5:7b-instruct)."),
    extract_missing: bool = typer.Option(False, help="Attempt text extraction for missing cache entries."),
) -> None:
    """Score Jobs_Today rows using cached job text, and update columns I:J."""
    from .config import load_config
    from .job_scoring_cached import score_unscored_sheet_rows_from_cache
    from .llm_score import DEFAULT_MODEL
    from .sheets_sync import SheetsConfig

    cfg = load_config()
    sheet_id = sheet_id or cfg.sheet_id
    sheet_tab = sheet_tab or cfg.jobs_today_tab

    if not sheet_id:
        console.print("sheet_id is required")
        raise typer.Exit(2)

    summary = score_unscored_sheet_rows_from_cache(
        db_path=Path("data") / "jobs.sqlite3",
        model=model.strip() or DEFAULT_MODEL,
        sheet_cfg=SheetsConfig(sheet_id=sheet_id, tab=sheet_tab, account=cfg.sheet_account),
        max_jobs=max_jobs,
        concurrency=concurrency,
        extract_missing=extract_missing,
    )

    # If there were errors, print a hint to enable more verbose debugging.
    if summary.get("errors"):
        console.print("note: scoring had errors. Next step: rerun with --concurrency 1 and we can add per-URL error prints.")

    console.print(
        f"scored={summary['scored']} updated_rows={summary['updated_rows']} missing={summary['missing']} errors={summary['errors']}"
    )


@app.command(name="score-unscored")
def score_unscored(
    sheet_id: str = typer.Option("", help="Google Sheet ID (or set SHEET_ID in data/config.env)."),
    sheet_tab: str = typer.Option("", help="Sheet tab to update (default Jobs_Today)."),
    batch_size: int = typer.Option(25, help="How many rows to score per batch."),
    max_batches: int = typer.Option(50, help="Safety cap on number of batches."),
    model: str = typer.Option("", help="Ollama model (default qwen2.5:7b-instruct)."),
) -> None:
    """Score all unscored rows currently present in Jobs_Today (loop in batches)."""

    from .config import load_config
    from .llm_score import DEFAULT_MODEL
    from .score_unscored_sheet import score_all_unscored_sheet_rows
    from .sheets_sync import SheetsConfig

    cfg = load_config()
    sheet_id = sheet_id or cfg.sheet_id
    sheet_tab = sheet_tab or cfg.jobs_today_tab

    if not sheet_id:
        console.print("sheet_id is required")
        raise typer.Exit(2)

    sheet_cfg = SheetsConfig(sheet_id=sheet_id, tab=sheet_tab, account=cfg.sheet_account)

    def _progress(batch_no: int, s: dict) -> None:
        console.print(f"batch={batch_no} candidates={s['candidates']} scored={s['scored']} updated={s['updated_rows']} errors={s['errors']}")

    summary = score_all_unscored_sheet_rows(
        sheet_cfg=sheet_cfg,
        model=model.strip() or DEFAULT_MODEL,
        batch_size=batch_size,
        max_batches=max_batches,
        sleep_s=0.5,
        progress_cb=_progress,
    )

    console.print(f"scored={summary['scored']} updated_rows={summary['updated_rows']} errors={summary['errors']}")


@app.command(name="push-all-jobs")
def push_all_jobs(
    sheet_id: str = typer.Option("", help="Google Sheet ID (or set SHEET_ID in data/config.env)."),
    tab: str = typer.Option("", help="Destination tab name (default ALL_JOBS_TAB)."),
) -> None:
    """Export SQLite -> CSV and push it to the All jobs sheet tab.

    This was removed from the automatic dashboard pipeline because it can be slow/noisy.
    """
    from .config import load_config
    from .export_all_jobs import ExportConfig, export_all_jobs_csv
    from .sheets_all_jobs import AllJobsSheetConfig, write_all_jobs_csv_to_sheet

    cfg = load_config()
    sheet_id = sheet_id or cfg.sheet_id
    tab = tab or cfg.all_jobs_tab

    if not sheet_id:
        console.print("sheet_id is required")
        raise typer.Exit(2)

    export_all_jobs_csv()
    csv_path = ExportConfig().out_csv
    uploaded = write_all_jobs_csv_to_sheet(AllJobsSheetConfig(sheet_id=sheet_id, tab=tab), csv_path)
    console.print(f"uploaded_rows={uploaded} tab={tab}")


@app.command(name="score-open-tabs")
def score_open_tabs(
    sheet_id: str = typer.Option("", help="Google Sheet ID (or set SHEET_ID in data/config.env)."),
    sheet_tab: str = typer.Option("", help="Sheet tab to update (default Jobs_Today)."),
    max_tabs: int = typer.Option(25, help="How many open tabs to consider (most recent first)."),
    model: str = typer.Option("", help="Ollama model (default qwen2.5:7b-instruct)."),
    dry_run: bool = typer.Option(False, help="Do not update the sheet, just print what would be updated."),
    open_unscored: bool = typer.Option(False, help="Open unscored sheet URLs in the CDP browser first (best-effort)."),
    sites: str = typer.Option("", help="Comma-separated host filters (e.g. tanitjobs.com,linkedin.com)."),
    max_open: int = typer.Option(20, help="Max tabs to open when --open-unscored is set."),
) -> None:
    """Manual workflow helper.

    Primary mode (default):
    - Reads currently open URLs in the CDP Chrome session (tabs you opened yourself)
    - Extracts text directly from the already-open tab (no navigation)
    - Writes it into job_text_cache
    - If the URL exists unscored in Jobs_Today, scores it and updates columns I:J

    Cloudflare workflow (optional):
    - Use --open-unscored to automatically open unscored sheet URLs in the CDP browser.
      Then you manually solve any challenges in Chrome.
      Then rerun score-open-tabs (without --open-unscored) to extract+score.

    Filtering:
    - Use --sites to restrict to specific hosts, e.g. "tanitjobs.com".

    Examples:
      jobformer score-open-tabs --open-unscored --sites tanitjobs.com --max-open 20
      # solve challenges in the CDP Chrome window
      jobformer score-open-tabs --sites tanitjobs.com
    """

    import os

    from .config import load_config
    from .cdp_open_tabs import extract_text_from_open_tabs, open_urls_in_cdp
    from .job_text_cache_db import JobTextCacheDB
    from .llm_score import DEFAULT_MODEL, score_job_with_ollama
    from .sheets_sync import SheetsConfig, _get_sheet_rows, update_job_scores
    from .url_canon import canonicalize_url

    cfg = load_config()
    sheet_id = sheet_id or cfg.sheet_id
    sheet_tab = sheet_tab or cfg.jobs_today_tab

    if not sheet_id:
        console.print("sheet_id is required")
        raise typer.Exit(2)

    cdp_url = (os.getenv("CDP_URL") or cfg.cdp_url or "").strip()
    if not cdp_url:
        console.print("CDP_URL not set")
        raise typer.Exit(2)

    model = model.strip() or (os.getenv("LLM_MODEL") or "").strip() or DEFAULT_MODEL

    # Read sheet and index unscored rows by URL (canonicalized).
    sheet_cfg = SheetsConfig(sheet_id=sheet_id, tab=sheet_tab, account=cfg.sheet_account)
    rows = _get_sheet_rows(sheet_cfg)

    canon_to_meta: dict[str, tuple[str, str, str]] = {}
    canon_to_sheet_url: dict[str, str] = {}

    # Optional host filters
    site_filters = [s.strip().lower() for s in (sites or "").split(",") if s.strip()]

    for r in (rows or [])[1:]:
        if len(r) < 7:
            continue
        url = (r[6] or "").strip()
        score = (r[8] or "").strip() if len(r) > 8 else ""
        if not url or score:
            continue

        host = ""
        try:
            from urllib.parse import urlparse

            host = (urlparse(url).netloc or "").lower()
        except Exception:
            host = ""

        if site_filters and not any(sf in host for sf in site_filters):
            continue

        title = (r[2] or "").strip() if len(r) > 2 else ""
        company = (r[3] or "").strip() if len(r) > 3 else ""
        location = (r[4] or "").strip() if len(r) > 4 else ""

        cu = canonicalize_url(url)
        canon_to_meta[cu] = (title, company, location)
        canon_to_sheet_url[cu] = url

    if not canon_to_meta:
        console.print("No unscored rows found in sheet (after filters).")
        raise typer.Exit(0)

    if open_unscored:
        urls_to_open = list(canon_to_sheet_url.values())
        opened = open_urls_in_cdp(cdp_url=cdp_url, urls=urls_to_open, max_open=max_open)
        console.print(f"opened_tabs={opened} (now solve any challenges in the browser if needed, then rerun score-open-tabs)")

    open_tabs = extract_text_from_open_tabs(cdp_url=cdp_url, max_tabs=max_tabs)
    if not open_tabs:
        console.print("No usable open tabs found in CDP session.")
        raise typer.Exit(1)

    # Cache extracted text.
    cache_db = JobTextCacheDB(Path("data") / "jobs.sqlite3")
    touched = 0
    blocked = 0

    for t in open_tabs:
        cu = canonicalize_url(t.url)
        if cu not in canon_to_meta:
            continue
        touched += 1
        if t.status != "ok":
            blocked += 1
        cache_db.upsert(
            url_canon=cu,
            url=t.url,
            text=t.text or "",
            method="cdp-open-tab",
            status=t.status,
            error=t.error,
        )

    cache_db.close()

    console.print(f"open_tabs={len(open_tabs)} matched_unscored={touched} cached_blocked={blocked}")

    # Score the ones that have ok cached text.
    cache_db = JobTextCacheDB(Path("data") / "jobs.sqlite3")
    updates = []
    for cu, (title, company, location) in canon_to_meta.items():
        row = cache_db.get(cu)
        if not row or row.get("status") != "ok":
            continue
        text = (row.get("text") or "").strip()
        if len(text) < 200:
            continue

        sheet_url = canon_to_sheet_url.get(cu) or (row.get("url") or "")
        llm = score_job_with_ollama(
            title=title,
            company=company,
            location=location,
            url=sheet_url,
            page_text=text,
            model=model,
        )
        updates.append({"url": sheet_url, "score": llm.score, "reasons": (llm.reasons[0] if llm.reasons else "")[:180]})

    cache_db.close()

    if not updates:
        console.print("No scorable open-tab URLs (maybe still blocked).")
        raise typer.Exit(0)

    if dry_run:
        console.print(f"dry_run updates={len(updates)}")
        for u in updates[:10]:
            console.print(f"- {u['score']} | {u['url']}")
        raise typer.Exit(0)

    n = update_job_scores(sheet_cfg, updates)
    console.print(f"updated_rows={n} scored={len(updates)}")


if __name__ == "__main__":
    app()
