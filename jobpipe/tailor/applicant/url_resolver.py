"""applicant/url_resolver.py — re-export shim (PR-4).

The implementation moved up one level to ``jobpipe.tailor.url_resolver``.
Unmigrated callers (tailor scripts) still import
``from applicant.url_resolver import resolve_application_url``; this
shim keeps those imports working until PR-9 finishes the cutover.
"""

from url_resolver import resolve_application_url  # noqa: F401

__all__ = ["resolve_application_url"]
