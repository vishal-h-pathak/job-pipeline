"""resolve_url — dispatch a pasted URL to the right per-ATS scraper.

Pipeline:

    pasted URL
        │
        ▼
    resolve_application_url            (follow aggregator redirects;
        │                               reuse the existing helper from
        │                               jobpipe.tailor.url_resolver)
        ▼
    detect_ats                         (shared ATS detection)
        │
        ▼
    ┌─────────────────────────────┐
    │ greenhouse → fetch_greenhouse_posting │   confidence='high'
    │ lever      → fetch_lever_posting      │   confidence='high'
    │ ashby      → fetch_ashby_posting      │   confidence='high'
    │ else       → fetch_generic_posting    │   confidence='low'
    └─────────────────────────────┘
        │
        ▼
    ScrapedPosting
"""

from __future__ import annotations

import logging

from jobpipe.shared.ats_detect import detect_ats
from jobpipe.tailor.url_resolver import (
    resolve_application_url,
    resolve_headless_enabled,
    resolve_to_ats_headless,
)

from . import ScrapedPosting
from .scrape_ashby import fetch_ashby_posting
from .scrape_generic import fetch_generic_posting
from .scrape_greenhouse import fetch_greenhouse_posting
from .scrape_lever import fetch_lever_posting

logger = logging.getLogger("tailor.manual.resolve")


def resolve_url(url: str) -> ScrapedPosting:
    """Resolve a pasted job URL into a ScrapedPosting.

    Raises:
        ScrapeError: any per-ATS HTTP/parse failure, OR the generic
            fallback can't recover a title.
    """
    resolved = resolve_application_url(url)
    target = resolved.get("resolved") or url
    if target != url:
        logger.info(
            "manual: aggregator-resolved %s → %s (%s)",
            url, target, resolved.get("notes", ""),
        )

    ats = detect_ats(target)

    # On-demand headless fallback: the static path couldn't identify an ATS
    # (still generic/aggregator). With JOBPIPE_RESOLVE_HEADLESS on, drive a real
    # browser through the JS Apply flow to capture the final ATS URL. Off by
    # default — this is a single-job, human-initiated paste, so the cost is
    # acceptable when opted in.
    if ats == "generic" and resolve_headless_enabled():
        headless_url = resolve_to_ats_headless(target)
        if headless_url:
            logger.info("manual: headless-resolved %s → %s", target, headless_url)
            target = headless_url
            ats = detect_ats(target)

    logger.info("manual: ats_kind=%s for %s", ats, target)

    if ats == "greenhouse":
        return fetch_greenhouse_posting(target)
    if ats == "lever":
        return fetch_lever_posting(target)
    if ats == "ashby":
        return fetch_ashby_posting(target)

    # Workday / iCIMS / SmartRecruiters / LinkedIn / aggregators / unknown.
    # All take the low-confidence generic path; the human verifies via
    # /dashboard/review/{job_id} before tailoring proceeds (Amendment 1).
    return fetch_generic_posting(target)
