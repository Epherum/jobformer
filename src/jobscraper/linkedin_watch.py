from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Optional

from jobscraper.sources.linkedin_minimal import LinkedInMinimalConfig, fetch_first_job_id


def load_state(path: Path) -> dict:
    if not path.exists():
        return {}
    return json.loads(path.read_text(encoding="utf-8"))


def save_state(path: Path, state: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(state, ensure_ascii=False, indent=2), encoding="utf-8")


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument(
        "--url",
        default=LinkedInMinimalConfig.url,
        help="LinkedIn jobs search URL (e.g. Tunisia + last 2h + sortBy=DD)",
    )
    ap.add_argument("--state", default="data/linkedin_state.json")
    args = ap.parse_args()

    state_path = Path(args.state)
    state = load_state(state_path)

    jid, reason = fetch_first_job_id(LinkedInMinimalConfig(url=args.url))
    if not jid:
        print(f"linkedin_watch: no job id ({reason})")
        return 2

    last = state.get("last_job_id")
    if last is None:
        state["last_job_id"] = jid
        save_state(state_path, state)
        print(f"linkedin_watch: initialized last_job_id={jid}")
        return 0

    if str(jid) != str(last):
        state["last_job_id"] = jid
        save_state(state_path, state)
        print(f"linkedin_watch: NEW first job id {jid} (was {last})")
        print(f"url: {args.url}")
        return 1

    print(f"linkedin_watch: unchanged ({jid})")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
