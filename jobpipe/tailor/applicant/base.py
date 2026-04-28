"""applicant/base.py — re-export shim (PR-7).

The implementation moved to ``jobpipe.submit.adapters.applicant_base``. The
move co-locates ``BaseApplicant`` (sync Playwright DOM applicants) with the
async ``Adapter`` base in ``jobpipe.submit.adapters.base`` so both form-fill
adapter bases share a package directory. There is no Python superclass
unification — see ``jobpipe/submit/adapters/applicant_base.py`` for why.

Unmigrated callers (legacy scripts and any bare-import call sites resolved
via ``jobpipe.shared.ats_detect._bootstrap_tailor_sys_path``) keep working
through this shim until PR-9 finishes the cutover.
"""

from jobpipe.submit.adapters.applicant_base import BaseApplicant  # noqa: F401

__all__ = ["BaseApplicant"]
