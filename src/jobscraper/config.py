from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path
from typing import Optional


DEFAULT_CONFIG_ENV = Path("data/config.env")


@dataclass(frozen=True)
class AppConfig:
    sheet_id: str = ""
    sheet_account: str = "wassimfekih2@gmail.com"
    jobs_tab: str = "Jobs"
    jobs_today_tab: str = "Jobs_Today"
    all_jobs_tab: str = "All jobs"
    # Default CDP URL from WSL -> Windows host.
    # Note: if Chrome binds only to 127.0.0.1 on Windows, use a portproxy rule.
    cdp_url: str = "http://172.25.192.1:9224"
    interval_min: int = 20


def _load_envfile(path: Path) -> None:
    if not path.exists():
        return
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        k, v = line.split("=", 1)
        os.environ.setdefault(k.strip(), v.strip())


def load_config(env_path: Path = DEFAULT_CONFIG_ENV) -> AppConfig:
    _load_envfile(env_path)

    def geti(name: str, default: int) -> int:
        v = (os.getenv(name) or "").strip()
        if not v:
            return default
        try:
            return int(v)
        except ValueError:
            return default

    return AppConfig(
        sheet_id=(os.getenv("SHEET_ID") or "").strip(),
        sheet_account=(os.getenv("SHEET_ACCOUNT") or AppConfig.sheet_account).strip(),
        jobs_tab=(os.getenv("JOBS_TAB") or AppConfig.jobs_tab).strip(),
        jobs_today_tab=(os.getenv("JOBS_TODAY_TAB") or AppConfig.jobs_today_tab).strip(),
        all_jobs_tab=(os.getenv("ALL_JOBS_TAB") or AppConfig.all_jobs_tab).strip(),
        cdp_url=(os.getenv("CDP_URL") or AppConfig.cdp_url).strip(),
        interval_min=geti("INTERVAL_MIN", AppConfig.interval_min),
    )
