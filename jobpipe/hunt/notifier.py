"""jobpipe.hunt.notifier — PR-8 shim. See ``jobpipe/notify.py`` for the
canonical implementation. Re-exports ``send_digest`` so the unprefixed
``from notifier import send_digest`` in ``jobpipe.hunt.agent`` keeps
resolving through the sys.path bootstrap PR-3 set up.

Module name kept as ``notifier`` (not ``notify``) for back-compat with
the hunt-side import; the canonical module is ``jobpipe.notify``.
"""
from __future__ import annotations

from jobpipe.notify import send_digest  # noqa: F401  PR-8 re-export


def __getattr__(name: str):
    """Forward any other attribute (FROM_ADDR, TO_ADDR, RESEND_URL) to
    ``jobpipe.notify`` so callers that introspect module constants keep
    working."""
    import jobpipe.notify as _canonical
    try:
        return getattr(_canonical, name)
    except AttributeError as exc:
        raise AttributeError(name) from exc
