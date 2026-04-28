"""jobpipe.tailor.notify — PR-8 shim. See ``jobpipe/notify.py`` for the
canonical implementation. Re-exports the tailor-side surface (canonical
``send_*`` names + the deprecated ``notify_*`` aliases) so the
unprefixed ``from notify import …`` in ``jobpipe.tailor.pipeline`` keeps
resolving through the sys.path bootstrap PR-4 set up.
"""
from __future__ import annotations

from jobpipe.notify import (  # noqa: F401  PR-8 re-exports
    cockpit_url,
    create_notification,
    # Canonical send_* names (PR-8).
    send_applied,
    send_awaiting_review,
    send_awaiting_submit,
    send_failed,
    # Deprecated notify_* aliases — kept so pre-PR-8 callers keep
    # working; each alias logs a once-per-process deprecation warning
    # before forwarding to the canonical send_* function.
    notify_applied,
    notify_awaiting_submit,
    notify_failed,
    notify_ready_for_review,
)


def __getattr__(name: str):
    """Forward any other attribute (PORTFOLIO_BASE_URL, etc.) to
    ``jobpipe.notify`` so callers that introspect module constants keep
    working."""
    import jobpipe.notify as _canonical
    try:
        return getattr(_canonical, name)
    except AttributeError as exc:
        raise AttributeError(name) from exc
