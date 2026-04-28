"""applicant/detector.py — Detect which ATS platform a job URL uses (M-3).

Routing layer. Each known ATS gets its own per-platform DOM-based
handler (Ashby, Greenhouse, Lever) that reads from `job["form_answers"]`
and never makes Anthropic API calls. Anything we don't have a handler
for falls through to `UniversalApplicant`, which drives the page via a
prepare-only Claude tool-use agent (M-4 strips its submit-mode tool).

The `USE_LEGACY_APPLICANTS` env flag is gone — per-ATS handlers are the
default and the cheap path. The vision agent is the fallback for the
messier middle of the funnel (Workday, iCIMS, SmartRecruiters,
aggregator-direct postings).
"""

from __future__ import annotations

import logging

logger = logging.getLogger("applicant.detector")


def detect_ats(url: str) -> str:
    """Identify the ATS platform from the application URL.

    Returns one of: ashby, greenhouse, lever, indeed, workday, linkedin,
    icims, smartrecruiters, generic.
    """
    url_lower = (url or "").lower()

    if "ashby" in url_lower or "ashbyhq.com" in url_lower or "ashby_jid" in url_lower:
        return "ashby"
    if (
        "greenhouse.io" in url_lower
        or "boards.greenhouse" in url_lower
        or "job-boards.greenhouse" in url_lower
    ):
        return "greenhouse"
    if "lever.co" in url_lower or "jobs.lever" in url_lower:
        return "lever"
    if "indeed.com/applystart" in url_lower or "indeed.com/viewjob" in url_lower:
        return "indeed"
    if "myworkdayjobs.com" in url_lower or "workday.com" in url_lower:
        return "workday"
    if "linkedin.com" in url_lower:
        return "linkedin"
    if "icims.com" in url_lower:
        return "icims"
    if "smartrecruiters.com" in url_lower:
        return "smartrecruiters"
    return "generic"


def get_applicant(url: str):
    """Return the appropriate applicant instance for a given URL.

    Per-ATS DOM handlers (zero Anthropic spend) are the default for
    Ashby, Greenhouse, and Lever. Everything else falls through to
    `UniversalApplicant`, which uses a Claude tool-use agent in
    prepare-only mode (M-4) to drive unknown ATSes by vision.
    """
    ats = detect_ats(url)
    logger.info(f"ats detected: {ats} for {url}")

    if ats == "ashby":
        from applicant.ashby import AshbyApplicant
        return AshbyApplicant()
    if ats == "greenhouse":
        from applicant.greenhouse import GreenhouseApplicant
        return GreenhouseApplicant()
    if ats == "lever":
        from applicant.lever import LeverApplicant
        return LeverApplicant()

    # Fallback for Workday / iCIMS / SmartRecruiters / Indeed / aggregators.
    # The vision agent is prepare-only — it can never click Submit (M-4).
    from applicant.universal import UniversalApplicant
    return UniversalApplicant()
