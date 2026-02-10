from __future__ import annotations

import datetime as dt
import re
from dataclasses import dataclass
from typing import List, Optional, Tuple

from ..cdp_session import get_cdp_browser, invalidate_cdp_browser
from ..models import Job


@dataclass
class AnetiConfig:
    cdp_url: str
    # "Cadres" results list (no criteria). This is where we saw stable offer-detail links.
    list_url: str = "https://www.emploi.nat.tn/fo/Fr/global.php?page=146&=true&FormLinks_Sorting=7&FormLinks_Sorted=7"
    max_offers: int = 25
    timeout_ms: int = 30_000


# Offer details use global.php?page=990&bureau=...&annee=...&numoffre=...
_DETAIL_RE = re.compile(r"global\.php\?page=990.*\bbureau=\d+.*\bannee=\d+.*\bnumoffre=\d+", re.I)
_DATE_RE = re.compile(r"\b(\d{2})/(\d{2})/(\d{4})\b")


def _abs(url: str, href: str) -> str:
    if href.startswith("http"):
        return href
    if href.startswith("/"):
        return "https://www.emploi.nat.tn" + href
    # relative
    base = "https://www.emploi.nat.tn/fo/Fr/"
    return base + href


def _parse_date_fr(text: str) -> Optional[dt.datetime]:
    # First dd/mm/yyyy in text.
    m = _DATE_RE.search(text or "")
    if not m:
        return None
    dd, mm, yyyy = m.group(1), m.group(2), m.group(3)
    try:
        return dt.datetime(int(yyyy), int(mm), int(dd), tzinfo=dt.timezone.utc)
    except Exception:
        return None


def _extract_from_row(row_text: str, url: str) -> Job:
    """Build a Job from a results-row text.

    The detail page seems to redirect to home unless authenticated.
    So for minimal mode we rely on the list table row.
    """
    posted_at = _parse_date_fr(row_text)

    # Clean whitespace and pick a usable title.
    lines = [ln.strip() for ln in (row_text or "").splitlines() if ln.strip()]

    # Many rows start with a reference like 2120/2026/150.
    title = "(unknown)"
    if lines:
        # Remove the reference itself.
        if re.fullmatch(r"\d{3,4}/\d{4}/\d+", lines[0]):
            lines = lines[1:]

    if lines:
        title = lines[0]

    # Remove trailing columns like "TUNIS 1 30/01/2026" if present.
    title = re.sub(r"\s+[A-ZÀ-Ÿ'\- ]+\s+\d+\s+\d{2}/\d{2}/\d{4}\s*$", "", title).strip() or title

    return Job(
        source="aneti",
        external_id=url,
        title=title,
        company="",
        location="",
        url=url,
        posted_at=posted_at,
    )


def scrape_aneti(cfg: AnetiConfig) -> Tuple[List[Job], str]:
    """Minimal ANETI scrape via CDP.

    Detail pages (page=990) appear to redirect to home (no content) unless
    authenticated / proper session. So minimal mode scrapes the *results list*
    and uses each row text as the job title.

    - Opens cadres list URL
    - Extracts first N offer links
    - For each link, captures its table row text and parses date if present
    """

    jobs: List[Job] = []

    try:
        browser = get_cdp_browser(
            cfg.cdp_url,
            timeout_ms=cfg.timeout_ms,
            retries=2,
            backoff_s=0.8,
            raise_on_fail=True,
        )
    except RuntimeError as e:
        return [], f"cdp_error: {e}"

    ctx = browser.contexts[0] if browser.contexts else browser.new_context()

    page = ctx.new_page()
    page.set_default_timeout(cfg.timeout_ms)
    try:
        page.goto(cfg.list_url, wait_until="domcontentloaded")
        page.wait_for_timeout(1500)

        body = page.inner_text("body") or ""
        if "Web Page Blocked" in body:
            return [], "blocked"

        # Grab offer links and their closest <tr> text.
        items = page.eval_on_selector_all(
            "a[href]",
            """
            els => {
              const out=[];
              for (const a of els) {
                const href=a.getAttribute('href')||'';
                if (!href.includes('global.php?page=990')) continue;
                if (!href.includes('bureau=')) continue;
                const tr=a.closest('tr');
                const rowText=tr ? (tr.innerText||'').trim() : (a.textContent||'').trim();
                out.push({href, rowText});
              }
              return out;
            }
            """,
        )

        links: List[Tuple[str, str]] = []
        for it in items:
            h = it.get('href') or ''
            if _DETAIL_RE.search(h):
                links.append((_abs(page.url, h), (it.get('rowText') or '').strip()))

        # De-dupe preserving order
        seen = set()
        links_u: List[Tuple[str, str]] = []
        for u, rowText in links:
            if u in seen:
                continue
            seen.add(u)
            links_u.append((u, rowText))

        for u, rowText in links_u[: cfg.max_offers]:
            jobs.append(_extract_from_row(rowText, u))

        return jobs, "cdp_list"
    except Exception:
        invalidate_cdp_browser()
        raise
    finally:
        try:
            page.close()
        except Exception:
            pass
