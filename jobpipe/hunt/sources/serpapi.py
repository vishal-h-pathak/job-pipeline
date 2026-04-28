"""
sources/serpapi.py — Google Jobs results via SerpAPI.

Cost discipline matters here: SerpAPI's free tier is 100 searches/month, and
the previous configuration burned ~72 searches per run (12 queries × 2 locs ×
3 pages). Three guards now enforce a per-run budget:

1. ``MAX_SEARCHES_PER_RUN`` hard cap. We stop iterating once we hit it and
   log a clear "budget exhausted" message rather than silently truncating.
2. Pagination short-circuits once a page yields nothing new (almost every
   page after the first is duplicates anyway).
3. Per-run logging of total searches issued so the budget shows up in
   ``agent.log``.

The query list and locations are mode-aware via ``config.get_mode()``.
"""

from __future__ import annotations

import logging
import os
import time

import requests

from jobpipe.config import get_mode
from jobpipe.shared.jobid import make_job_id

logger = logging.getLogger("sources.serpapi")

QUERIES = [
    "neuromorphic engineer",
    "computational neuroscience",
    "spiking neural network",
    "connectomics",
    "sales engineer AI startup",
    "solutions engineer machine learning",
    "developer relations AI",
]

# Mode-aware locations. ``label`` is what we record on the row; ``location``
# is what SerpAPI sees; ``remote`` flag appends "remote" to the query so
# Google biases toward remote-friendly listings.
_LOCAL_REMOTE_LOCATIONS = (
    {"location": "Atlanta, Georgia, United States", "label": "Atlanta, GA"},
    {"location": "United States", "label": "Remote", "remote": True},
)
_US_EXTRA_LOCATIONS = (
    {"location": "United States", "label": "United States"},
)

ENDPOINT = "https://serpapi.com/search.json"

# Hard cap so a single run can't burn the whole monthly free tier. Default
# dropped 30 → 15 → 8 as more free sources came online (Ashby, HN, 80kh,
# JSearch). At 8/run × ~30 days that's 240/month — well inside the SerpAPI
# free tier (100/month) when you also factor in days you don't run. Override
# via the env var if you want a wider sweep.
MAX_SEARCHES_PER_RUN = int(os.environ.get("SERPAPI_MAX_SEARCHES", "8"))

# Cap pages per (query, location). Most relevant results appear on page 1.
MAX_PAGES = 2


def _locations_for_mode():
    if get_mode() == "us_wide":
        return _LOCAL_REMOTE_LOCATIONS + _US_EXTRA_LOCATIONS
    return _LOCAL_REMOTE_LOCATIONS


def fetch():
    """Yield job dicts from SerpAPI's Google Jobs endpoint with pagination."""
    api_key = os.environ.get("SERPAPI_KEY")
    if not api_key:
        logger.warning("SERPAPI_KEY not set — skipping serpapi source")
        return

    locations = _locations_for_mode()
    seen_local: set[str] = set()
    searches_issued = 0
    yielded = 0

    budget_exhausted = False
    for q in QUERIES:
        if budget_exhausted:
            break
        for loc in locations:
            if budget_exhausted:
                break
            query = f"{q} remote" if loc.get("remote") else q
            for page in range(MAX_PAGES):
                if searches_issued >= MAX_SEARCHES_PER_RUN:
                    logger.warning(
                        "serpapi: budget exhausted at %d searches "
                        "(MAX_SEARCHES_PER_RUN=%d) — stopping",
                        searches_issued, MAX_SEARCHES_PER_RUN,
                    )
                    budget_exhausted = True
                    break

                params = {
                    "engine": "google_jobs",
                    "q": query,
                    "location": loc["location"],
                    "api_key": api_key,
                    "start": page * 10,
                }
                try:
                    resp = requests.get(ENDPOINT, params=params, timeout=30)
                    searches_issued += 1
                    resp.raise_for_status()
                    data = resp.json()
                except Exception as exc:
                    logger.warning(
                        "serpapi: request failed q=%r loc=%r page=%d: %s",
                        query, loc["label"], page, exc,
                    )
                    time.sleep(1)
                    break  # stop paginating on error

                results = data.get("jobs_results", []) or []
                if not results:
                    logger.info("serpapi: 0 results q=%r loc=%r page=%d (stop)",
                                query, loc["label"], page)
                    break  # no more pages

                page_yield = 0
                for job in results:
                    title = job.get("title", "")
                    company = job.get("company_name", "Unknown")
                    location = job.get("location", loc["label"])
                    description = job.get("description", "")
                    link = ""
                    for opt in job.get("apply_options", []) or []:
                        if opt.get("link"):
                            link = opt["link"]
                            break
                    if not link:
                        link = job.get("share_link") or job.get("job_id", "")
                    jid = make_job_id(link, title, company)
                    if jid in seen_local:
                        continue
                    seen_local.add(jid)
                    page_yield += 1
                    yielded += 1
                    yield {
                        "id": jid,
                        "source": "serpapi",
                        "query": q,
                        "title": title,
                        "company": company,
                        "location": location,
                        "description": description,
                        "url": link,
                    }
                logger.info(
                    "serpapi: page yielded %d new q=%r loc=%r page=%d",
                    page_yield, query, loc["label"], page,
                )
                # Short-circuit if a page produced nothing new — pagination
                # past that point is almost certainly more dupes.
                if page_yield == 0:
                    break
                time.sleep(1)

    logger.info(
        "serpapi total: %d unique entries from %d searches (budget=%d)",
        yielded, searches_issued, MAX_SEARCHES_PER_RUN,
    )
