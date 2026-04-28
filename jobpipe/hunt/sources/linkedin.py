"""LinkedIn source — uses SerpAPI Google Jobs with site:linkedin.com filtering.

KEEP-DISABLED (PR-3): this module is intentionally NOT included in the
``SOURCES`` tuple in ``hunt/agent.py``. Two clean runs returned 0
results, and the JSearch source already covers LinkedIn-via-RapidAPI
behind the same paid subscription that pays for Indeed coverage. We
keep the file on disk because the SerpAPI ``site:linkedin.com/jobs``
query shape and the per-(query, location) budget pattern remain useful
documentation; re-enabling means flipping it back into the ``SOURCES``
tuple, not ripping the file out.

This piggybacks on the same SERPAPI_KEY as the main serpapi source but runs
a smaller set of high-signal queries specifically filtered to LinkedIn
postings, which tend to have richer descriptions and more accurate metadata.

Shares the per-run search budget with ``serpapi.py`` via the
``SERPAPI_MAX_SEARCHES`` env var; this module reads its own
``LINKEDIN_MAX_SEARCHES`` so the two sources don't fight over the same cap.
"""

from __future__ import annotations

import logging
import os
import time

import requests

from config import get_mode
from utils.jobid import make_job_id

logger = logging.getLogger("sources.linkedin")

# Focused queries — fewer than main serpapi to avoid API budget bloat.
QUERIES = [
    "neuromorphic engineer",
    "computational neuroscience",
    "spiking neural network engineer",
    "connectomics researcher",
    "BCI engineer",
    "sales engineer AI startup",
    "solutions engineer machine learning",
]

_LOCAL_REMOTE_LOCATIONS = (
    {"location": "Atlanta, Georgia, United States", "label": "Atlanta, GA"},
    {"location": "United States", "label": "Remote", "remote": True},
)
_US_EXTRA_LOCATIONS = (
    {"location": "United States", "label": "United States"},
)

ENDPOINT = "https://serpapi.com/search.json"
MAX_PAGES = 1  # LinkedIn results past page 1 rarely add signal.

# Independent budget so the LinkedIn-flavoured queries don't crowd out the
# main ``serpapi.py`` budget when both run in the same agent invocation.
# Halved alongside the main SerpAPI budget once Ashby/HN/80kh/expanded
# Greenhouse landed. Override via env if you want a wider LinkedIn sweep.
MAX_SEARCHES_PER_RUN = int(os.environ.get("LINKEDIN_MAX_SEARCHES", "8"))


def _locations_for_mode():
    if get_mode() == "us_wide":
        return _LOCAL_REMOTE_LOCATIONS + _US_EXTRA_LOCATIONS
    return _LOCAL_REMOTE_LOCATIONS


def fetch():
    """Yield job dicts from LinkedIn postings via SerpAPI Google Jobs."""
    api_key = os.environ.get("SERPAPI_KEY")
    if not api_key:
        logger.warning("SERPAPI_KEY not set — skipping linkedin source")
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
            query = f"site:linkedin.com/jobs {q}"
            if loc.get("remote"):
                query += " remote"
            for page in range(MAX_PAGES):
                if searches_issued >= MAX_SEARCHES_PER_RUN:
                    logger.warning(
                        "linkedin: budget exhausted at %d searches "
                        "(LINKEDIN_MAX_SEARCHES=%d) — stopping",
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
                        "linkedin: request failed q=%r loc=%r page=%d: %s",
                        query, loc["label"], page, exc,
                    )
                    time.sleep(1)
                    break

                results = data.get("jobs_results", []) or []
                if not results:
                    logger.info(
                        "linkedin: 0 results q=%r loc=%r page=%d (stop)",
                        query, loc["label"], page,
                    )
                    break

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
                        "source": "linkedin",
                        "query": q,
                        "title": title,
                        "company": company,
                        "location": location,
                        "description": description,
                        "url": link,
                    }
                logger.info(
                    "linkedin: page yielded %d new q=%r loc=%r page=%d",
                    page_yield, query, loc["label"], page,
                )
                if page_yield == 0:
                    break
                time.sleep(1)

    logger.info(
        "linkedin total: %d unique entries from %d searches (budget=%d)",
        yielded, searches_issued, MAX_SEARCHES_PER_RUN,
    )
