"""sources/ashby.py — AshbyHQ public posting API (J-1).

Direct Ashby public-API scanner:
    https://api.ashbyhq.com/posting-api/job-board/{slug}?includeCompensation=true

Pure HTTP/JSON. No LLM tokens spent on discovery. Each posting is
title-pre-filtered against `profile/portals.yml::title_filter` before
the LLM scorer ever sees it.

Company list lives in `profile/portals.yml::ashby.companies`. The
in-module `_FALLBACK_COMPANIES` is consulted only if portals.yml is
missing.
"""

from __future__ import annotations

import logging

from config import is_local_or_remote
from jobpipe.shared.html import strip_tags
from sources._http import (
    fetch_json,
    location_filter_enabled,
    passes_title_filter,
    sleep_between_requests,
)
from sources._portals import companies, title_signals
from utils.jobid import make_job_id

logger = logging.getLogger("sources.ashby")


_FALLBACK_COMPANIES: list[tuple[str, str]] = []


def _fetch_one(slug: str, display_name: str):
    """Fetch open roles from one Ashby board."""
    url = f"https://api.ashbyhq.com/posting-api/job-board/{slug}?includeCompensation=true"
    data = fetch_json(url, log=logger, label=slug)
    if data is None:
        return

    raw = 0
    yielded = 0
    skipped_loc = 0
    skipped_title = 0
    for job in data.get("jobs", []):
        if not job.get("isListed", True):
            continue
        raw += 1
        title = job.get("title") or ""
        location = job.get("location") or "Unknown"
        description = strip_tags(job.get("descriptionHtml") or job.get("descriptionPlain") or "")
        link = job.get("jobUrl") or job.get("applyUrl") or ""

        if not passes_title_filter(title):
            skipped_title += 1
            continue
        if location_filter_enabled() and not is_local_or_remote(location):
            skipped_loc += 1
            continue

        signals = title_signals(title)
        if signals["prefer"] or signals["seniority"]:
            logger.debug("ashby: %s title signals %s on %r", slug, signals, title)

        yielded += 1
        yield {
            "id": make_job_id(link, title, display_name),
            "source": "ashby",
            "query": "",
            "title": title,
            "company": display_name,
            "location": location,
            "description": description[:3000],
            "url": link,
        }
    logger.info(
        "ashby: %s yielded=%d (raw=%d, title-filtered=%d, location-filtered=%d)",
        slug, yielded, raw, skipped_title, skipped_loc,
    )


def fetch():
    """Yield job dicts from every tracked Ashby board."""
    targets = companies("ashby") or _FALLBACK_COMPANIES
    for slug, name in targets:
        yield from _fetch_one(slug, name)
        sleep_between_requests()
