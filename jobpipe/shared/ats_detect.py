"""jobpipe.shared.ats_detect — Detect which ATS platform a job URL uses (M-3).

Routing layer used by tailor's prepare flow and (eventually) submit's
deterministic flow. Each known ATS gets its own per-platform DOM-based
handler (Ashby, Greenhouse, Lever) that reads from ``job["form_answers"]``
and never makes Anthropic API calls. Anything we don't have a handler
for falls through to ``UniversalApplicant``, which drives the page via a
prepare-only Claude tool-use agent (M-4 strips its submit-mode tool).

The ``USE_LEGACY_APPLICANTS`` env flag is gone — per-ATS handlers are the
default and the cheap path. The vision agent is the fallback for the
messier middle of the funnel (Workday, iCIMS, SmartRecruiters,
aggregator-direct postings).

Moved from ``jobpipe/tailor/applicant/detector.py`` in PR-4 because the
routing decision is shared infrastructure, not tailor-specific. The
``applicant/detector.py`` path remains as a re-export shim until PR-9.
"""

from __future__ import annotations

import logging

logger = logging.getLogger("jobpipe.shared.ats_detect")


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


def _bootstrap_tailor_sys_path() -> None:
    """Ensure ``jobpipe/tailor/`` is on sys.path before importing prepare_dom adapters.

    The moved adapters in ``jobpipe.submit.adapters.prepare_dom.*`` still
    use bare imports (``from applicant.base import BaseApplicant``,
    ``from config import OUTPUT_DIR``) inherited from the legacy
    job-applicant repo. Those resolve only when ``jobpipe/tailor/`` is on
    sys.path. PR-7 moves base.py / browser_tools.py into the submit
    subtree and removes this bootstrap; until then, callers of
    get_applicant() depend on this stub running first.
    """
    import sys
    from pathlib import Path

    tailor_dir = str(Path(__file__).resolve().parents[1] / "tailor")
    if tailor_dir not in sys.path:
        sys.path.insert(0, tailor_dir)


def get_applicant(url: str):
    """Return the appropriate applicant instance for a given URL.

    Per-ATS DOM handlers (zero Anthropic spend) are the default for
    Ashby, Greenhouse, and Lever. Everything else falls through to
    ``UniversalApplicant``, which uses a Claude tool-use agent in
    prepare-only mode (M-4) to drive unknown ATSes by vision.
    """
    ats = detect_ats(url)
    logger.info(f"ats detected: {ats} for {url}")

    _bootstrap_tailor_sys_path()

    if ats == "ashby":
        from jobpipe.submit.adapters.prepare_dom.ashby import AshbyApplicant
        return AshbyApplicant()
    if ats == "greenhouse":
        from jobpipe.submit.adapters.prepare_dom.greenhouse import GreenhouseApplicant
        return GreenhouseApplicant()
    if ats == "lever":
        from jobpipe.submit.adapters.prepare_dom.lever import LeverApplicant
        return LeverApplicant()

    # Fallback for Workday / iCIMS / SmartRecruiters / Indeed / aggregators.
    # The vision agent is prepare-only — it can never click Submit (M-4).
    from jobpipe.submit.adapters.prepare_dom.universal import UniversalApplicant
    return UniversalApplicant()
