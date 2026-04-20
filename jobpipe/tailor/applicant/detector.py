"""
applicant/detector.py — Detect which ATS platform a job URL uses.

The UniversalApplicant handles any ATS via a Claude tool-use agent, so this
module is mostly for logging / metrics now. It still returns the right applicant
when USE_LEGACY_APPLICANTS=true is set in the env (falls back to AshbyApplicant
for Ashby URLs).
"""

import logging
import os

logger = logging.getLogger("applicant.detector")

from applicant.universal import UniversalApplicant


def detect_ats(url: str) -> str:
    """
    Identify the ATS platform from the application URL.

    Returns one of: ashby, greenhouse, lever, indeed, workday, linkedin, icims,
    smartrecruiters, generic.
    """
    url_lower = (url or "").lower()

    if "ashby" in url_lower or "ashbyhq.com" in url_lower or "ashby_jid" in url_lower:
        return "ashby"
    elif "greenhouse.io" in url_lower or "boards.greenhouse" in url_lower:
        return "greenhouse"
    elif "lever.co" in url_lower or "jobs.lever" in url_lower:
        return "lever"
    elif "indeed.com/applystart" in url_lower or "indeed.com/viewjob" in url_lower:
        return "indeed"
    elif "myworkdayjobs.com" in url_lower or "workday.com" in url_lower:
        return "workday"
    elif "linkedin.com" in url_lower:
        return "linkedin"
    elif "icims.com" in url_lower:
        return "icims"
    elif "smartrecruiters.com" in url_lower:
        return "smartrecruiters"
    else:
        return "generic"


def get_applicant(url: str):
    """
    Return the appropriate applicant instance for a given URL.

    Default: UniversalApplicant for every URL (Claude-driven, ATS-agnostic).
    Legacy mode: set USE_LEGACY_APPLICANTS=true in env to route Ashby URLs to
    the legacy AshbyApplicant.
    """
    ats = detect_ats(url)
    logger.info(f"ats detected: {ats} for {url}")

    if os.getenv("USE_LEGACY_APPLICANTS", "").lower() == "true":
        if ats == "ashby":
            from applicant.ashby import AshbyApplicant
            return AshbyApplicant()

    # Default: one universal applicant
    return UniversalApplicant()
