from __future__ import annotations

import argparse
import json
from pathlib import Path

from jobscraper.filtering import is_relevant
from jobscraper.sources.aneti import AnetiConfig, scrape_aneti


def load_state(path: Path) -> dict:
    if not path.exists():
        return {}
    return json.loads(path.read_text(encoding="utf-8"))


def save_state(path: Path, state: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(state, ensure_ascii=False, indent=2), encoding="utf-8")


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--cdp", required=True, help="CDP url, e.g. http://172.25.192.1:9223")
    ap.add_argument("--state", default="data/aneti_state.json")
    ap.add_argument("--max-offers", type=int, default=50, help="Safety cap. Still only first page.")
    args = ap.parse_args()

    cfg = AnetiConfig(cdp_url=args.cdp, max_offers=args.max_offers)
    jobs, _ = scrape_aneti(cfg)

    if not jobs:
        print("aneti_watch: no jobs")
        return 2

    current_ids = [j.external_id for j in jobs]

    state_path = Path(args.state)
    state = load_state(state_path)
    prev_ids = state.get("seen_ids")

    if prev_ids is None:
        state["seen_ids"] = current_ids
        save_state(state_path, state)
        print(f"aneti_watch: initialized seen_ids={len(current_ids)}")
        return 0

    prev_set = set(prev_ids)
    new_jobs = [j for j in jobs if j.external_id not in prev_set]
    new_relevant = [j for j in new_jobs if is_relevant(j.title)]

    state["seen_ids"] = current_ids
    save_state(state_path, state)

    if new_relevant:
        print(f"aneti_watch: NEW relevant={len(new_relevant)} (new_total={len(new_jobs)})")
        for j in new_relevant[:10]:
            print(f"NEW: {j.title} | {j.url}")
        return 1

    print(f"aneti_watch: no new relevant (new_total={len(new_jobs)})")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
