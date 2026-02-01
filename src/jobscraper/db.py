from __future__ import annotations

import sqlite3
from dataclasses import asdict
from datetime import datetime
from pathlib import Path
from typing import Iterable, List

from .models import Job


SCHEMA = """
CREATE TABLE IF NOT EXISTS jobs (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  source TEXT NOT NULL,
  external_id TEXT NOT NULL,
  fingerprint TEXT NOT NULL,
  title TEXT NOT NULL,
  company TEXT NOT NULL,
  location TEXT NOT NULL,
  url TEXT NOT NULL,
  posted_at TEXT,
  first_seen_at TEXT NOT NULL,
  last_seen_at TEXT NOT NULL
);

CREATE UNIQUE INDEX IF NOT EXISTS ux_jobs_source_external_id
  ON jobs(source, external_id);

CREATE INDEX IF NOT EXISTS ix_jobs_first_seen_at
  ON jobs(first_seen_at);
"""


class JobDB:
    def __init__(self, path: str | Path):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.conn = sqlite3.connect(str(self.path))
        self.conn.row_factory = sqlite3.Row
        self._init()

    def _init(self) -> None:
        self.conn.executescript(SCHEMA)
        self.conn.commit()

    def upsert_jobs(self, jobs: Iterable[Job]) -> List[Job]:
        """Insert new jobs, update last_seen_at for existing jobs.

        Returns: list of newly inserted jobs.
        """
        now = datetime.utcnow().isoformat(timespec="seconds") + "Z"
        new_jobs: List[Job] = []

        cur = self.conn.cursor()
        for job in jobs:
            posted_at = job.posted_at.isoformat(timespec="seconds") + "Z" if job.posted_at else None

            # Try insert. If conflict, update last_seen_at.
            try:
                cur.execute(
                    """
                    INSERT INTO jobs (
                      source, external_id, fingerprint, title, company, location, url,
                      posted_at, first_seen_at, last_seen_at
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        job.source,
                        job.external_id,
                        job.fingerprint,
                        job.title,
                        job.company,
                        job.location,
                        job.url,
                        posted_at,
                        now,
                        now,
                    ),
                )
                new_jobs.append(job)
            except sqlite3.IntegrityError:
                cur.execute(
                    """
                    UPDATE jobs
                    SET last_seen_at = ?
                    WHERE source = ? AND external_id = ?
                    """,
                    (now, job.source, job.external_id),
                )

        self.conn.commit()
        return new_jobs

    def close(self) -> None:
        self.conn.close()
