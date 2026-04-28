"""applicant/universal.py — re-export shim (PR-4).

The implementation moved to ``jobpipe.submit.adapters.prepare_dom.universal``.
Unmigrated callers (tailor scripts test_local_form / test_beacon /
submit_one) keep working through this shim until PR-9.
"""

from jobpipe.submit.adapters.prepare_dom.universal import UniversalApplicant  # noqa: F401

__all__ = ["UniversalApplicant"]
