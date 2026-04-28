"""applicant/ashby.py — re-export shim (PR-4).

The implementation moved to ``jobpipe.submit.adapters.prepare_dom.ashby``.
Unmigrated callers (legacy scripts, any test referencing
``applicant.ashby.AshbyApplicant`` or the ``_build_field_map`` helper)
keep working through this shim until PR-9 finishes the cutover.
"""

from jobpipe.submit.adapters.prepare_dom.ashby import (  # noqa: F401
    AshbyApplicant,
    _build_field_map,
)

__all__ = ["AshbyApplicant", "_build_field_map"]
