"""jobpipe.submit.db — PR-8 shim. See ``jobpipe/db.py`` for the canonical
implementation. Re-exports the submit-side surface so unprefixed
``import db`` from ``jobpipe.submit.runner`` and the test fixtures keeps
resolving through the sys.path bootstrap PR-5 set up.
"""
from __future__ import annotations

from jobpipe.db import (  # noqa: F401  PR-8 re-exports
    close_attempt,
    get_job,
    get_jobs_ready_for_submission,
    mark_failed,
    mark_needs_review,
    mark_submitted,
    mark_submitting,
    next_attempt_n,
    open_attempt,
    record_submission_log,
    verify_materials_hash,
)


def __getattr__(name: str):
    """Forward any other attribute (e.g. ``client``, ``service_client``)
    to ``jobpipe.db`` so the test fixture's ``db.client = fake`` /
    ``db.service_client = fake`` overrides land transparently and any
    cross-subtree symbol lookup keeps working."""
    import jobpipe.db as _canonical
    try:
        return getattr(_canonical, name)
    except AttributeError as exc:
        raise AttributeError(name) from exc
