"""jobpipe.tailor.db — PR-8 shim. See ``jobpipe/db.py`` for the canonical
implementation. Re-exports the tailor-side surface so the unprefixed
``from db import …`` in ``jobpipe.tailor.pipeline`` and the test fixtures
keep resolving through the sys.path bootstrap PR-4 set up.
"""
from __future__ import annotations

from jobpipe.db import (  # noqa: F401  PR-8 re-exports
    delete_job_materials,
    get_approved_jobs,
    get_confirmed_jobs,
    get_job_counts_by_status,
    get_jobs_by_status,
    get_prefill_requested_jobs,
    mark_applied,
    mark_awaiting_submit,
    mark_prefilling,
    mark_preparing,
    mark_ready_for_review,
    mark_ready_to_submit,
    mark_skipped,
    mark_tailor_failed,
    update_job_status,
)


def __getattr__(name: str):
    """Forward any other attribute (e.g. ``client``) to ``jobpipe.db`` so
    ``from db import client as _db_client`` in ``tailor/pipeline.py`` and
    the test fixture's ``db.client = fake`` override resolve transparently."""
    import jobpipe.db as _canonical
    try:
        return getattr(_canonical, name)
    except AttributeError as exc:
        raise AttributeError(name) from exc
