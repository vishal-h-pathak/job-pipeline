"""
config.py — central configuration for the job-hunter.

Two operating modes:

- ``local_remote`` (default) — only pulls jobs whose location is Atlanta, GA
  or fully remote / US-remote. This is the high-signal mode used day-to-day.
- ``us_wide`` — also pulls non-remote roles across the United States.
  Useful when you want a wide sweep before drilling down.

Set ``HUNTER_MODE=us_wide`` in the env to switch, or pass ``--mode us_wide``
to ``job_agent.py``. ``get_mode()`` resolves the value lazily so tests can
override it after import.
"""

from __future__ import annotations

import os
from typing import Literal

Mode = Literal["local_remote", "us_wide"]
DEFAULT_MODE: Mode = "local_remote"


# Sentinel allowing the orchestrator to set the mode at startup so each
# source module can read it without re-parsing CLI args.
_ACTIVE_MODE: Mode | None = None


def set_mode(mode: Mode) -> None:
    """Override the active hunter mode for this process."""
    global _ACTIVE_MODE
    if mode not in ("local_remote", "us_wide"):
        raise ValueError(f"unknown HUNTER_MODE: {mode!r}")
    _ACTIVE_MODE = mode


def get_mode() -> Mode:
    """Return the active hunter mode.

    Resolution order:
        1. set_mode() override
        2. HUNTER_MODE env var
        3. DEFAULT_MODE
    """
    if _ACTIVE_MODE is not None:
        return _ACTIVE_MODE
    env = os.environ.get("HUNTER_MODE", "").strip().lower()
    if env in ("local_remote", "us_wide"):
        return env  # type: ignore[return-value]
    return DEFAULT_MODE


# ── Mode-aware helpers ────────────────────────────────────────────────────

# Atlanta-area locations recognised when filtering Greenhouse/Lever boards
# in local_remote mode.
LOCAL_LOCATION_SUBSTRINGS = (
    "atlanta",
    "georgia",
    "ga,",
    " ga",
    "ga/",
)

REMOTE_LOCATION_SUBSTRINGS = (
    "remote",
    "anywhere",
    "distributed",
    "work from home",
    "wfh",
    "us-remote",
    "us remote",
    "global",
)


def is_local_or_remote(location: str | None) -> bool:
    """True if the location string looks like Atlanta or a remote role.

    The check is intentionally generous — Greenhouse boards have wildly
    inconsistent location strings ("Remote - US", "Atlanta, GA / Remote",
    "United States (Remote)") so we look for any matching substring rather
    than requiring an exact match. Empty / null locations are treated as
    "unknown but probably ok" so they aren't dropped silently.
    """
    if not location:
        return True
    s = location.lower()
    if any(needle in s for needle in REMOTE_LOCATION_SUBSTRINGS):
        return True
    if any(needle in s for needle in LOCAL_LOCATION_SUBSTRINGS):
        return True
    return False


def location_filter_enabled() -> bool:
    """Should sources filter their results down to Atlanta/Remote?"""
    return get_mode() == "local_remote"
