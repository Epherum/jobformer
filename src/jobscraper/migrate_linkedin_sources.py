from __future__ import annotations

import re
import sqlite3
from dataclasses import replace
from datetime import datetime, timezone
from pathlib import Path


def _parse_iso_z(s: str | None) -> datetime | None:
    if not s:
        return None
    try:
        if s.endswith("Z"):
            s = s[:-1] + "+00:00"
        return datetime.fromisoformat(s).astimezone(timezone.utc)
    except Exception:
        return None


TN_HINTS = [
    "tunis",
    "sfax",
    "sousse",
    "nabeul",
    "ariana",
    "ben arous",
    "bizerte",
    "tunisia",
    "tunisie",
]
FR_HINTS = [
    "france",
    "paris",
    "île-de-france",
    "ile-de-france",
    "lyon",
    "marseille",
    "lille",
    "toulouse",
    "bordeaux",
    "nantes",
    "montpellier",
]
DE_HINTS = [
    "germany",
    "deutschland",
    "berlin",
    "munich",
    "münchen",
    "hamburg",
    "frankfurt",
    "köln",
    "cologne",
    "stuttgart",
    "düsseldorf",
    "dusseldorf",
]


def guess_label(location: str | None) -> str:
    loc = (location or "").strip().lower()
    for h in TN_HINTS:
        if h in loc:
            return "TN"
    for h in FR_HINTS:
        if h in loc:
            return "FR"
    for h in DE_HINTS:
        if h in loc:
            return "GR"  # dashboard label for Germany
    return "LI"


def migrate(db_path: Path) -> dict:
    con = sqlite3.connect(str(db_path))
    cur = con.cursor()

    # Find rows with legacy source='linkedin'
    cur.execute(
        "SELECT id, source, location, first_seen_at FROM jobs WHERE source = 'linkedin'"
    )
    rows = cur.fetchall()

    cutoff = datetime(2026, 2, 2, 0, 0, 0, tzinfo=timezone.utc)

    updates = []
    for job_id, source, location, first_seen_at in rows:
        dt = _parse_iso_z(first_seen_at)
        if dt and dt < cutoff:
            label = "TN"  # user assumption: pre-2026-02-02 mostly Tunisia
        else:
            label = guess_label(location)
        updates.append((f"linkedin {label}", job_id))

    cur.executemany("UPDATE jobs SET source = ? WHERE id = ?", updates)
    con.commit()

    # sanity counts
    cur.execute("SELECT source, COUNT(*) FROM jobs WHERE source LIKE 'linkedin%' GROUP BY source")
    counts = dict(cur.fetchall())

    con.close()
    return {"updated": len(updates), "counts": counts}


if __name__ == "__main__":
    res = migrate(Path("data/jobs.sqlite3"))
    print(res)
