"""applicant/lever.py — re-export shim (PR-4).

The implementation moved to ``jobpipe.submit.adapters.prepare_dom.lever``.
Unmigrated callers keep working through this shim until PR-9.
"""

from jobpipe.submit.adapters.prepare_dom.lever import LeverApplicant  # noqa: F401

__all__ = ["LeverApplicant"]
