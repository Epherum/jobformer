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
                # Update last_seen_at always. Also improve metadata if we previously had placeholders.
                cur.execute(
                    "SELECT title, posted_at FROM jobs WHERE source = ? AND external_id = ?",
                    (job.source, job.external_id),
                )
                row = cur.fetchone()
                existing_title = (row[0] if row else "") or ""
                existing_posted_at = row[1] if row else None

                new_title = job.title
                # If we accidentally stored garbage titles, upgrade them.
                bad_title = (
                    (existing_title.strip() in {"", "(unknown)"})
                    or ("annonces trouv" in existing_title.lower())
                    or ("offres et demandes" in existing_title.lower())
                    or ("offres disponibles" in existing_title.lower())
                )

                set_title = new_title if (bad_title and new_title and new_title != "(unknown)") else existing_title
                set_posted_at = posted_at if (existing_posted_at is None and posted_at is not None) else existing_posted_at

                cur.execute(
                    """
                    UPDATE jobs
                    SET last_seen_at = ?,
                        title = ?,
                        company = CASE WHEN company = '' THEN ? ELSE company END,
                        location = CASE WHEN location = '' THEN ? ELSE location END,
                        url = ?,
                        posted_at = ?
                    WHERE source = ? AND external_id = ?
                    """,
                    (now, set_title, job.company, job.location, job.url, set_posted_at, job.source, job.external_id),
                )

        self.conn.commit()
        return new_jobs

    def close(self) -> None:
        self.conn.close()
