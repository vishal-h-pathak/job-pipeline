"""jobpipe.hunt.config — PR-8 shim. See ``jobpipe/config.py`` for the
canonical implementation. Re-exports hunter-mode helpers so the
unprefixed ``import config`` in ``jobpipe.hunt.agent`` keeps resolving
through the sys.path bootstrap PR-3 set up.
"""
from __future__ import annotations

from jobpipe.config import (  # noqa: F401  PR-8 re-exports
    DEFAULT_MODE,
    LOCAL_LOCATION_SUBSTRINGS,
    Mode,
    REMOTE_LOCATION_SUBSTRINGS,
    get_mode,
    is_local_or_remote,
    location_filter_enabled,
    set_mode,
)


def __getattr__(name: str):
    """Forward any other attribute to ``jobpipe.config`` so any
    cross-subtree env constant lookup keeps working."""
    import jobpipe.config as _canonical
    try:
        return getattr(_canonical, name)
    except AttributeError as exc:
        raise AttributeError(name) from exc
