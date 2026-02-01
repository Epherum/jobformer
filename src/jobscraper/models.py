from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Optional


@dataclass(frozen=True)
class Job:
    source: str
    external_id: str
    title: str
    company: str
    location: str
    url: str
    posted_at: Optional[datetime] = None

    @property
    def fingerprint(self) -> str:
        # A stable fallback in case external_id is missing/unstable.
        key = "|".join(
            [
                self.source.strip().lower(),
                self.external_id.strip().lower(),
                self.title.strip().lower(),
                self.company.strip().lower(),
                self.location.strip().lower(),
                self.url.strip().lower(),
            ]
        )
        return key
