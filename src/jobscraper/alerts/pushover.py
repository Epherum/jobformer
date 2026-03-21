from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable, Optional

import requests


API_URL = "https://api.pushover.net/1/messages.json"


@dataclass(frozen=True)
class PushoverConfig:
    user_key: str
    app_token: str


def load_from_envfile(path: Path = Path("data/pushover.env")) -> Optional[PushoverConfig]:
    if path.exists():
        for line in path.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            k, v = line.split("=", 1)
            os.environ.setdefault(k.strip(), v.strip())

    user = (os.getenv("PUSHOVER_USER_KEY") or "").strip()
    token = (os.getenv("PUSHOVER_APP_TOKEN") or "").strip()
    if not user or not token:
        return None
    return PushoverConfig(user_key=user, app_token=token)


def send(
    *,
    title: str,
    message: str,
    url: str = "",
    url_title: str = "",
    priority: int = 0,
    sound: str = "",
    cfg: Optional[PushoverConfig] = None,
    timeout_s: int = 15,
) -> None:
    cfg = cfg or load_from_envfile()
    if not cfg:
        raise RuntimeError("Pushover not configured. Set data/pushover.env with PUSHOVER_USER_KEY and PUSHOVER_APP_TOKEN")

    data = {
        "token": cfg.app_token,
        "user": cfg.user_key,
        "title": title,
        "message": message,
        "priority": str(priority),
    }
    if url:
        data["url"] = url
    if url_title:
        data["url_title"] = url_title
    if sound:
        data["sound"] = sound

    resp = requests.post(API_URL, data=data, timeout=timeout_s)
    resp.raise_for_status()


def send_summary(
    *,
    title: str,
    lines: list[str],
    click_url: str = "",
    click_title: str = "Open",
    max_chars: int = 950,
    priority: int = 0,
    sound: str = "",
) -> None:
    # Pushover message limit is 1024 chars. Keep headroom.
    msg = "\n".join(lines)
    if len(msg) > max_chars:
        # truncate cleanly
        msg = msg[: max_chars - 20].rsplit("\n", 1)[0] + "\n…(truncated)"

    send(title=title, message=msg, url=click_url, url_title=click_title, priority=priority, sound=sound)
