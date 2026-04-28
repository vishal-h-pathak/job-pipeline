"""applicant/greenhouse.py — re-export shim (PR-4).

The implementation moved to ``jobpipe.submit.adapters.prepare_dom.greenhouse``.
Unmigrated callers keep working through this shim until PR-9.
"""

from jobpipe.submit.adapters.prepare_dom.greenhouse import GreenhouseApplicant  # noqa: F401

__all__ = ["GreenhouseApplicant"]
