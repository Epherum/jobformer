from __future__ import annotations

import sqlite3
from datetime import datetime
from pathlib import Path
from typing import Dict, Iterable, Optional


SCHEMA = """
CREATE TABLE IF NOT EXISTS job_text_cache (
  url_canon TEXT PRIMARY KEY,
  url TEXT,
  text TEXT,
  method TEXT,
  fetched_at TEXT NOT NULL,
  status TEXT NOT NULL,
  error TEXT
);

CREATE INDEX IF NOT EXISTS ix_job_text_cache_status
  ON job_text_cache(status);

CREATE INDEX IF NOT EXISTS ix_job_text_cache_url
  ON job_text_cache(url);
"""


class JobTextCacheDB:
    def __init__(self, path: str | Path):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.conn = sqlite3.connect(str(self.path))
        self.conn.row_factory = sqlite3.Row
        try:
            self.conn.execute("PRAGMA journal_mode=WAL;")
            self.conn.execute("PRAGMA synchronous=NORMAL;")
            self.conn.execute("PRAGMA busy_timeout=3000;")
        except Exception:
            pass
        self._init()

    def _init(self) -> None:
        # Lightweight migration from older schema where primary key was `url`.
        cur = self.conn.execute("PRAGMA table_info(job_text_cache)")
        cols = [r[1] for r in cur.fetchall()]
        if cols and "url_canon" not in cols:
            # Old table exists. Migrate to new schema.
            self.conn.execute("ALTER TABLE job_text_cache RENAME TO job_text_cache_old")
            self.conn.executescript(SCHEMA)
            try:
                self.conn.execute(
                    """
                    INSERT INTO job_text_cache (url_canon, url, text, method, fetched_at, status, error)
                    SELECT url as url_canon, url, text, method, fetched_at, status, error
                    FROM job_text_cache_old
                    """
                )
            except Exception:
                pass
            self.conn.execute("DROP TABLE job_text_cache_old")
            self.conn.commit()
            return

        self.conn.executescript(SCHEMA)
        self.conn.commit()

    def get(self, url_canon: str) -> Optional[dict]:
        cur = self.conn.execute("SELECT * FROM job_text_cache WHERE url_canon = ?", (url_canon,))
        row = cur.fetchone()
        return dict(row) if row else None

    def get_many(self, url_canons: Iterable[str]) -> Dict[str, dict]:
        url_canons = [u for u in url_canons if u]
        if not url_canons:
            return {}
        q = "SELECT * FROM job_text_cache WHERE url_canon IN (%s)" % (",".join("?" * len(url_canons)))
        cur = self.conn.execute(q, url_canons)
        rows = cur.fetchall()
        return {row["url_canon"]: dict(row) for row in rows}

    def upsert(
        self,
        *,
        url_canon: str,
        url: str,
        text: str,
        method: str,
        status: str,
        error: str | None = None,
    ) -> None:
        now = datetime.utcnow().isoformat(timespec="seconds") + "Z"
        cur = self.conn.cursor()
        try:
            cur.execute(
                """
                INSERT INTO job_text_cache (url_canon, url, text, method, fetched_at, status, error)
                VALUES (?, ?, ?, ?, ?, ?, ?)
                """,
                (url_canon, url, text, method, now, status, error),
            )
        except sqlite3.IntegrityError:
            cur.execute(
                """
                UPDATE job_text_cache
                SET url = ?, text = ?, method = ?, fetched_at = ?, status = ?, error = ?
                WHERE url_canon = ?
                """,
                (url, text, method, now, status, error, url_canon),
            )
        self.conn.commit()

    def close(self) -> None:
        self.conn.close()
