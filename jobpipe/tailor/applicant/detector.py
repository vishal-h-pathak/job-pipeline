"""applicant/detector.py — re-export shim (PR-4).

The implementation moved to ``jobpipe.shared.ats_detect``. Unmigrated
callers (tailor scripts: tailor_one, submit_one, test_local_form,
test_beacon) still import ``from applicant.detector import detect_ats,
get_applicant``; this shim keeps those imports working until PR-9
finishes the cutover.
"""

from jobpipe.shared.ats_detect import detect_ats, get_applicant  # noqa: F401

__all__ = ["detect_ats", "get_applicant"]
