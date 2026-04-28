"""jobpipe.hunt.db — PR-8 shim. See ``jobpipe/db.py`` for the canonical
implementation. Re-exports the hunt-side surface so the unprefixed
``from db import get_seen_ids, upsert_job`` in ``jobpipe.hunt.agent``
keeps resolving through the sys.path bootstrap PR-3 set up.
"""
from __future__ import annotations

from jobpipe.db import (  # noqa: F401  PR-8 re-export
    get_seen_ids,
    upsert_job,
)


def __getattr__(name: str):
    """Forward any other attribute (e.g. ``client``) to ``jobpipe.db`` so
    new symbols added there don't require a shim update."""
    import jobpipe.db as _canonical
    try:
        return getattr(_canonical, name)
    except AttributeError as exc:
        raise AttributeError(name) from exc
