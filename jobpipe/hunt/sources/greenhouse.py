"""sources/greenhouse.py — Greenhouse boards-api source (J-1).

Direct Greenhouse public-API scanner:
    https://boards-api.greenhouse.io/v1/boards/{slug}/jobs?content=true

Pure HTTP/JSON. No LLM tokens spent on discovery. Each posting is
title-pre-filtered against `profile/portals.yml::title_filter` before
the LLM scorer ever sees it.

Company list lives in `profile/portals.yml::greenhouse.companies`. The
in-module `_FALLBACK_COMPANIES` is consulted only if portals.yml is
missing — keeps the hunter running during cutover.

Mode handling: in `local_remote` mode roles whose location is neither
Atlanta/GA nor remote-shaped are skipped. `us_wide` keeps everything.
"""

import logging

from jobpipe.config import is_local_or_remote
from jobpipe.shared.html import strip_tags
from jobpipe.shared.jobid import make_job_id
from sources._http import (
    fetch_json,
    location_filter_enabled,
    passes_title_filter,
    sleep_between_requests,
)
from sources._portals import companies, title_signals

logger = logging.getLogger("sources.greenhouse")


# Last-resort fallback for when portals.yml is missing. Keep this list
# tiny — the canonical list lives in portals.yml.
_FALLBACK_COMPANIES = [
    ("anthropic", "Anthropic"),
    ("neuralink", "Neuralink"),
]


def _fetch_one(slug: str, display_name: str):
    """Fetch open roles from a single Greenhouse board."""
    url = f"https://boards-api.greenhouse.io/v1/boards/{slug}/jobs?content=true"
    data = fetch_json(url, log=logger, label=slug)
    if data is None:
        return

    raw = 0
    yielded = 0
    skipped_loc = 0
    skipped_title = 0
    for job in data.get("jobs", []):
        raw += 1
        title = job.get("title", "")
        location = job.get("location", {}).get("name", "Unknown")
        description = strip_tags(job.get("content", ""))
        link = job.get("absolute_url", "")

        # Cheap title-only filter before we look at description or score.
        if not passes_title_filter(title):
            skipped_title += 1
            continue

        if location_filter_enabled() and not is_local_or_remote(location):
            skipped_loc += 1
            continue

        signals = title_signals(title)
        if signals["prefer"] or signals["seniority"]:
            logger.debug("greenhouse: %s title signals %s on %r", slug, signals, title)

        yielded += 1
        yield {
            "id": make_job_id(link, title, display_name),
            "source": "greenhouse",
            "query": "",
            "title": title,
            "company": display_name,
            "location": location,
            "description": description[:3000],
            "url": link,
        }
    logger.info(
        "greenhouse: %s yielded=%d (raw=%d, title-filtered=%d, location-filtered=%d)",
        slug, yielded, raw, skipped_title, skipped_loc,
    )


def fetch():
    """Yield job dicts from every Greenhouse board listed in portals.yml."""
    targets = companies("greenhouse") or _FALLBACK_COMPANIES
    for slug, name in targets:
        yield from _fetch_one(slug, name)
        sleep_between_requests()
