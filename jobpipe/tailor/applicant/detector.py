"""
applicant/detector.py — Detect which ATS platform a job URL uses.
"""

import logging

logger = logging.getLogger("applicant.detector")

# Import applicants here as they're implemented
# from applicant.greenhouse import GreenhouseApplicant
# from applicant.lever import LeverApplicant
# from applicant.indeed import IndeedApplicant
# from applicant.generic import GenericApplicant


def detect_ats(url: str) -> str:
    """
    Identify the ATS platform from the application URL.

    Returns one of: greenhouse, lever, indeed, workday, linkedin, generic
    """
    url_lower = url.lower()

    if "greenhouse.io" in url_lower or "boards.greenhouse" in url_lower:
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

    Returns None for platforms that require manual application (e.g., LinkedIn).
    """
    ats = detect_ats(url)

    if ats == "linkedin":
        logger.info("LinkedIn detected — requires manual application")
        return None

    # As applicants are implemented, uncomment:
    # if ats == "greenhouse":
    #     return GreenhouseApplicant()
    # elif ats == "lever":
    #     return LeverApplicant()
    # elif ats == "indeed":
    #     return IndeedApplicant()

    logger.warning(f"No applicant implemented for ATS: {ats} — using generic")
    # return GenericApplicant()
    return None  # Until we implement the first applicant
