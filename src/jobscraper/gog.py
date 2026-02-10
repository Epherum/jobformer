from __future__ import annotations

import os
import subprocess
from typing import List


def _gog_env() -> dict:
    env = os.environ.copy()
    # Avoid Node deprecation warnings being treated as failures.
    env["NODE_NO_WARNINGS"] = "1"
    # Override any --throw-deprecation in NODE_OPTIONS for gog runs.
    env["NODE_OPTIONS"] = "--no-deprecation --no-warnings"
    return env


def run_gog(args: List[str], *, check: bool = True) -> subprocess.CompletedProcess:
    proc = subprocess.run(args, capture_output=True, text=True, env=_gog_env())
    if check and proc.returncode != 0:
        raise RuntimeError(f"gog failed: {' '.join(args)}\n{proc.stderr}\n{proc.stdout}")
    return proc
