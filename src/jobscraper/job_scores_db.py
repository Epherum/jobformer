from __future__ import annotations

import json
import sqlite3
from datetime import datetime
from pathlib import Path
from typing import Dict, Iterable, Optional


SCHEMA = """
CREATE TABLE IF NOT EXISTS job_scores (
  url TEXT PRIMARY KEY,
  score REAL,
  decision TEXT,
  reasons TEXT,
  model TEXT,
  created_at TEXT NOT NULL,
  updated_at TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS ix_job_scores_decision
  ON job_scores(decision);
"""


class JobScoresDB:
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
        self.conn.executescript(SCHEMA)
        self.conn.commit()

    def get(self, url: str) -> Optional[dict]:
        cur = self.conn.execute("SELECT * FROM job_scores WHERE url = ?", (url,))
        row = cur.fetchone()
        if not row:
            return None
        return dict(row)

    def get_many(self, urls: Iterable[str]) -> Dict[str, dict]:
        urls = [u for u in urls if u]
        if not urls:
            return {}
        q = "SELECT * FROM job_scores WHERE url IN (%s)" % (",".join("?" * len(urls)))
        cur = self.conn.execute(q, urls)
        rows = cur.fetchall()
        return {row["url"]: dict(row) for row in rows}

    def upsert_score(self, *, url: str, score: float, decision: str, reasons: list[str] | str, model: str) -> None:
        now = datetime.utcnow().isoformat(timespec="seconds") + "Z"
        if isinstance(reasons, list):
            reasons_json = json.dumps(reasons, ensure_ascii=False)
        else:
            reasons_json = json.dumps([reasons], ensure_ascii=False)

        cur = self.conn.cursor()
        try:
            cur.execute(
                """
                INSERT INTO job_scores (url, score, decision, reasons, model, created_at, updated_at)
                VALUES (?, ?, ?, ?, ?, ?, ?)
                """,
                (url, score, decision, reasons_json, model, now, now),
            )
        except sqlite3.IntegrityError:
            cur.execute(
                """
                UPDATE job_scores
                SET score = ?, decision = ?, reasons = ?, model = ?, updated_at = ?
                WHERE url = ?
                """,
                (score, decision, reasons_json, model, now, url),
            )
        self.conn.commit()

    def close(self) -> None:
        self.conn.close()
