"""sources/jsearch.py — JSearch (RapidAPI) Google Jobs aggregator.

JSearch wraps Google Jobs the same way SerpAPI does, but with a few useful
differences for our use case:

1. Each request returns up to 20 jobs across LinkedIn, Indeed, Glassdoor,
   ZipRecruiter, BeBee, Talent.com, and other publishers. One subscription
   replaces both the broken Indeed RSS source and the broken LinkedIn-via-
   SerpAPI source.
2. The free tier (200 requests/month) is enough for a daily run with a
   tight budget. Basic ($10/mo) gives 1,500/month — comfortable margin.
3. Pricing is per-request, not per-page, so one ``num_pages=2`` call costs
   the same as ``num_pages=1`` but returns twice the jobs.

Setup:
  1. Subscribe to JSearch on RapidAPI:
       https://rapidapi.com/letscrape-6bRBa3QguO5/api/jsearch
  2. Copy your "X-RapidAPI-Key" from the dashboard.
  3. Set ``JSEARCH_API_KEY=...`` in ``.env``.
  4. Optional: ``JSEARCH_MAX_REQUESTS_PER_RUN`` (default 8) caps spend per
     run. Each request returns up to 20 jobs across multiple publishers.

If ``JSEARCH_API_KEY`` is unset, the source no-ops with a single warning so
the rest of the run is unaffected — you can ship this code before signing
up.
"""

from __future__ import annotations

import logging
import os
import time

import requests

from config import get_mode
from utils.jobid import make_job_id

logger = logging.getLogger("sources.jsearch")

ENDPOINT = "https://jsearch.p.rapidapi.com/search"

# Same role-shape queries as the other sources. JSearch performs reasonable
# semantic matching, so we don't need the matrix of (query × location) the
# Google Jobs sources use — we let JSearch handle location via the query.
QUERIES = [
    "neuromorphic engineer",
    "computational neuroscience",
    "spiking neural network",
    "connectomics researcher",
    "BCI engineer",
    "sales engineer AI startup",
    "solutions engineer machine learning",
    "developer relations AI",
]

# Hard cap so one run can't burn the monthly tier. Default sized for the
# Basic plan (1,500/month) at one daily run. Override via env if you upgrade.
MAX_REQUESTS_PER_RUN = int(os.environ.get("JSEARCH_MAX_REQUESTS_PER_RUN", "8"))

# Per-request page count. Each "page" = ~10 jobs. ``num_pages=2`` returns
# up to 20 jobs in a single billable request.
NUM_PAGES_PER_REQUEST = int(os.environ.get("JSEARCH_NUM_PAGES", "2"))


def _query_for_mode(base_query: str) -> tuple[str, dict]:
    """Build the JSearch query + extra params for the active hunter mode.

    local_remote → biases toward Atlanta or remote roles via a single call
    with ``remote_jobs_only=false`` and an Atlanta hint in the query, plus
    a separate remote-only call. ``us_wide`` drops the location qualifier
    so JSearch returns nationwide hits.
    """
    if get_mode() == "us_wide":
        return base_query, {}
    # local_remote: append Atlanta + remote-friendly hint. JSearch tokenises
    # this naturally; testing showed it doesn't drop matches when the hint
    # is general.
    return f"{base_query} Atlanta OR remote", {"remote_jobs_only": "true"}


def fetch():
    """Yield job dicts from JSearch's aggregated Google Jobs feed."""
    api_key = os.environ.get("JSEARCH_API_KEY")
    if not api_key:
        logger.info("jsearch: JSEARCH_API_KEY not set — skipping (this is fine)")
        return

    headers = {
        "X-RapidAPI-Key": api_key,
        "X-RapidAPI-Host": "jsearch.p.rapidapi.com",
    }

    seen_local: set[str] = set()
    requests_issued = 0
    yielded = 0

    for q in QUERIES:
        if requests_issued >= MAX_REQUESTS_PER_RUN:
            logger.warning(
                "jsearch: budget exhausted at %d requests "
                "(MAX_REQUESTS_PER_RUN=%d) — stopping",
                requests_issued, MAX_REQUESTS_PER_RUN,
            )
            break

        query, extra_params = _query_for_mode(q)
        params = {
            "query": query,
            "page": "1",
            "num_pages": str(NUM_PAGES_PER_REQUEST),
            "country": "us",
            "date_posted": "month",
            **extra_params,
        }
        try:
            resp = requests.get(ENDPOINT, headers=headers, params=params, timeout=30)
            requests_issued += 1
            resp.raise_for_status()
            data = resp.json()
        except Exception as exc:
            logger.warning("jsearch: request failed q=%r: %s", query, exc)
            time.sleep(1)
            continue

        results = data.get("data") or []
        if not results:
            logger.info("jsearch: 0 results q=%r", query)
            continue

        page_yield = 0
        for job in results:
            title = job.get("job_title") or ""
            company = job.get("employer_name") or "Unknown"
            description = job.get("job_description") or ""
            link = job.get("job_apply_link") or ""

            # Build a human location string from JSearch's split fields.
            city = job.get("job_city") or ""
            state = job.get("job_state") or ""
            country = job.get("job_country") or ""
            is_remote = bool(job.get("job_is_remote"))
            if is_remote:
                location = "Remote"
            elif city and state:
                location = f"{city}, {state}"
            elif state and country:
                location = f"{state}, {country}"
            else:
                location = city or state or country or "Unknown"

            jid = make_job_id(link, title, company)
            if jid in seen_local:
                continue
            seen_local.add(jid)
            page_yield += 1
            yielded += 1
            yield {
                "id": jid,
                "source": "jsearch",
                "query": q,
                "title": title,
                "company": company,
                "location": location,
                "description": description[:3000],
                "url": link,
            }
        logger.info("jsearch: q=%r yielded %d new (publisher=%s)",
                    query, page_yield,
                    results[0].get("job_publisher", "?") if results else "?")
        time.sleep(1)

    logger.info(
        "jsearch total: %d unique entries from %d requests (budget=%d)",
        yielded, requests_issued, MAX_REQUESTS_PER_RUN,
    )
