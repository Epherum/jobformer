from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path
from typing import Optional


def _default_env_paths() -> list[Path]:
    """Search order for config.env.

    Supports running `jobformer` from anywhere (pipx/global install) by using a
    stable user directory, while keeping backward-compat with repo-local config.
    """

    # 1) Explicit override
    p = (os.getenv("JOBFORMER_CONFIG") or "").strip()
    if p:
        return [Path(p)]

    # 2) Repo-local (when running inside the repo)
    local = Path.cwd() / "data" / "config.env"

    # 3) User-local (global install)
    home = Path.home() / ".jobformer" / "config.env"
    xdg = Path(os.getenv("XDG_CONFIG_HOME") or (Path.home() / ".config")) / "jobformer" / "config.env"

    return [local, home, xdg]


def find_config_env() -> Path:
    for p in _default_env_paths():
        if p.exists():
            return p
    # default to ~/.jobformer/config.env for a clean global experience
    return Path.home() / ".jobformer" / "config.env"


DEFAULT_CONFIG_ENV = find_config_env()


@dataclass(frozen=True)
class AppConfig:
    # Base directory for relative paths (data/, state/, debug/)
    base_dir: Path

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


def load_config(env_path: Optional[Path] = None) -> AppConfig:
    env_path = env_path or find_config_env()
    _load_envfile(env_path)

    def geti(name: str, default: int) -> int:
        v = (os.getenv(name) or "").strip()
        if not v:
            return default
        try:
            return int(v)
        except ValueError:
            return default

    # Derive a base directory that makes relative paths work.
    # - If config is repo-local at <base>/data/config.env => base=<base>
    # - Otherwise base is the directory containing config.env (e.g. ~/.jobformer)
    if env_path.name == "config.env" and env_path.parent.name == "data":
        base_dir = env_path.parent.parent
    else:
        base_dir = env_path.parent

    # Ensure the expected folders exist when running globally.
    try:
        (base_dir / "data").mkdir(parents=True, exist_ok=True)
        (base_dir / "state").mkdir(parents=True, exist_ok=True)
        (base_dir / "debug").mkdir(parents=True, exist_ok=True)
    except Exception:
        pass

    return AppConfig(
        base_dir=base_dir,
        sheet_id=(os.getenv("SHEET_ID") or "").strip(),
        sheet_account=(os.getenv("SHEET_ACCOUNT") or AppConfig.sheet_account).strip(),
        jobs_tab=(os.getenv("JOBS_TAB") or AppConfig.jobs_tab).strip(),
        jobs_today_tab=(os.getenv("JOBS_TODAY_TAB") or AppConfig.jobs_today_tab).strip(),
        all_jobs_tab=(os.getenv("ALL_JOBS_TAB") or AppConfig.all_jobs_tab).strip(),
        cdp_url=(os.getenv("CDP_URL") or AppConfig.cdp_url).strip(),
        interval_min=geti("INTERVAL_MIN", AppConfig.interval_min),
    )
