from __future__ import annotations

import os
from collections import defaultdict
from urllib.parse import urlparse

from .cdp_page_fetch import fetch_page_text_via_cdp
from .sheets_sync import SheetsConfig, _get_sheet_rows


def pick_one_unscored_per_domain(cfg: SheetsConfig, limit_domains: int = 10) -> list[tuple[str, str]]:
    rows = _get_sheet_rows(cfg)
    out: list[tuple[str, str]] = []
    seen: set[str] = set()

    for r in rows[1:]:
        title = (r[2] or '').strip() if len(r) > 2 else ''
        url = (r[6] or '').strip() if len(r) > 6 else ''
        score = (r[9] or '').strip() if len(r) > 9 else ''
        # If the row is shorter than the LLM columns, treat it as unscored.
        if not url or score:
            continue

        dom = (urlparse(url).netloc or '').lower().replace('www.', '')
        if not dom or dom in seen:
            continue
        seen.add(dom)
        out.append((title, url))
        if len(out) >= limit_domains:
            break

    return out


def main() -> int:
    sheet_id = (os.getenv('SHEET_ID') or '').strip()
    tab = (os.getenv('JOBS_TODAY_TAB') or 'Jobs_Today').strip()
    account = (os.getenv('SHEET_ACCOUNT') or 'wassimfekih2@gmail.com').strip()
    cdp_url = (os.getenv('CDP_URL') or '').strip()

    if not sheet_id:
        print('Missing SHEET_ID')
        return 2
    if not cdp_url:
        print('Missing CDP_URL')
        return 2

    cfg = SheetsConfig(sheet_id=sheet_id, tab=tab, account=account)
    picks = pick_one_unscored_per_domain(cfg, limit_domains=20)
    if not picks:
        print('No unscored rows found.')
        return 0

    for title, url in picks:
        dom = (urlparse(url).netloc or '').lower().replace('www.', '')
        text = fetch_page_text_via_cdp(url, cdp_url, timeout_ms=45_000, max_chars=2000)
        print('---')
        print(dom)
        print(title)
        print(url)
        print('cdp_text_len', len(text))
        print(text[:220].replace('\n', ' '))

    return 0


if __name__ == '__main__':
    raise SystemExit(main())
