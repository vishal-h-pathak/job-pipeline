"""sources/workday.py — Workday public job-search API (J-1).

Workday tenants expose a public job-search endpoint at:

    https://<tenant>.wd<dc>.myworkdayjobs.com/wday/cxs/<tenant>/<site>/jobs

It accepts a JSON POST with `appliedFacets`, `limit`, and `offset`. We
walk pages until we run out of results or hit a configured cap. No LLM
tokens spent on discovery; each posting passes through the title pre-
filter before reaching the scorer.

Tenant config lives in `profile/portals.yml::workday.companies`. Each
row needs at minimum `tenant`, `site`, `dc`, and `name`. Per-row
`limit_pages` caps how many pages we walk for that tenant (default 2 —
50 jobs each, so 100 jobs/tenant).

Workday details are tenant-specific. If a row 404s or 500s, we log a
warning and continue. Add new rows incrementally; verify the careers
URL in a browser first.
"""

from __future__ import annotations

import logging
import re
import time
from typing import Iterable

import requests

from config import is_local_or_remote, location_filter_enabled
from sources._portals import passes_title_filter, title_signals, workday_tenants
from utils.jobid import make_job_id

logger = logging.getLogger("sources.workday")

TAG_RE = re.compile(r"<[^>]+>")
USER_AGENT = "job-hunter/1.0 (+https://vishal.pa.thak.io)"

# Default pagination; per-tenant rows can override via `limit_pages`.
PAGE_SIZE = 20
DEFAULT_LIMIT_PAGES = 2


def _strip_html(text: str) -> str:
    return TAG_RE.sub("", text or "").strip()


def _post_search(tenant: str, site: str, dc: str, offset: int) -> dict:
    """Hit the Workday job-search endpoint for one page."""
    url = (
        f"https://{tenant}.{dc}.myworkdayjobs.com/wday/cxs/{tenant}/{site}/jobs"
    )
    body = {"appliedFacets": {}, "limit": PAGE_SIZE, "offset": offset, "searchText": ""}
    headers = {"User-Agent": USER_AGENT, "Accept": "application/json"}
    resp = requests.post(url, json=body, headers=headers, timeout=20)
    resp.raise_for_status()
    return resp.json() or {}


def _fetch_job_detail(tenant: str, site: str, dc: str, external_path: str) -> dict:
    """Fetch full description for a single Workday job posting."""
    url = (
        f"https://{tenant}.{dc}.myworkdayjobs.com/wday/cxs/{tenant}/{site}{external_path}"
    )
    headers = {"User-Agent": USER_AGENT, "Accept": "application/json"}
    resp = requests.get(url, headers=headers, timeout=20)
    if resp.status_code != 200:
        return {}
    return resp.json() or {}


def _fetch_one(row: dict) -> Iterable[dict]:
    """Yield jobs for one Workday tenant config row."""
    tenant = (row.get("tenant") or "").strip()
    site = (row.get("site") or "").strip()
    dc = (row.get("dc") or "wd1").strip()
    name = (row.get("name") or tenant).strip()
    limit_pages = int(row.get("limit_pages") or DEFAULT_LIMIT_PAGES)

    if not tenant or not site:
        logger.warning("workday: skipping malformed row %r", row)
        return

    raw = 0
    yielded = 0
    skipped_loc = 0
    skipped_title = 0

    for page in range(limit_pages):
        offset = page * PAGE_SIZE
        try:
            data = _post_search(tenant, site, dc, offset)
        except Exception as exc:
            logger.warning(
                "workday: page %d fetch failed for %s/%s (%s): %s",
                page, tenant, site, dc, exc,
            )
            break

        postings = data.get("jobPostings") or []
        if not postings:
            break

        for p in postings:
            raw += 1
            title = p.get("title") or ""
            location = p.get("locationsText") or "Unknown"
            external_path = p.get("externalPath") or ""

            if not passes_title_filter(title):
                skipped_title += 1
                continue
            if location_filter_enabled() and not is_local_or_remote(location):
                skipped_loc += 1
                continue

            # Detail call — only for postings that survived the cheap filters.
            description = ""
            if external_path:
                detail = _fetch_job_detail(tenant, site, dc, external_path)
                jp = (detail.get("jobPostingInfo") or {})
                description = _strip_html(jp.get("jobDescription") or "")

            link = (
                f"https://{tenant}.{dc}.myworkdayjobs.com/{site}{external_path}"
                if external_path
                else f"https://{tenant}.{dc}.myworkdayjobs.com/{site}"
            )

            signals = title_signals(title)
            if signals["prefer"] or signals["seniority"]:
                logger.debug("workday: %s/%s title signals %s on %r", tenant, site, signals, title)

            yielded += 1
            yield {
                "id": make_job_id(link, title, name),
                "source": "workday",
                "query": "",
                "title": title,
                "company": name,
                "location": location,
                "description": description[:3000],
                "url": link,
            }

        time.sleep(0.5)

    logger.info(
        "workday: %s/%s yielded=%d (raw=%d, title-filtered=%d, location-filtered=%d)",
        tenant, site, yielded, raw, skipped_title, skipped_loc,
    )


def fetch():
    """Yield job dicts from every Workday tenant in portals.yml."""
    for row in workday_tenants():
        yield from _fetch_one(row)
        time.sleep(0.5)
