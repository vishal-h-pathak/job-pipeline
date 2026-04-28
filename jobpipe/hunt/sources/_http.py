"""sources/_http.py — polite-scraping helpers shared across hunt sources.

Three patterns used to live copy-pasted across ashby/greenhouse/lever and
(less rigorously) the other source modules:

  1. ``requests.get(url, timeout=N)`` wrapped in a try/except that logs and
     returns on failure, plus a 404-as-warning short-circuit so removed ATS
     boards don't blow up the run.
  2. A ``time.sleep`` between requests so we look like a polite client.
  3. Single import surface for ``passes_title_filter`` (from ``_portals``)
     and ``location_filter_enabled`` (from ``config``) — convenience so a
     source module doesn't import from three different modules just to do
     its filtering.

Each per-source ``_fetch_one`` shrinks to its real per-API logic
(URL template, auth header, pagination) once these helpers absorb the
boilerplate. The adapter family stays — we deliberately do not collapse
the per-source ``_fetch_one`` functions into one.
"""

from __future__ import annotations

import logging
import time
from typing import Any, Optional

import requests

# Re-exports so callers can do ``from sources._http import ...`` for filters.
from config import location_filter_enabled  # noqa: F401  re-export
from sources._portals import passes_title_filter  # noqa: F401  re-export


def fetch_json(
    url: str,
    *,
    timeout: int = 15,
    headers: Optional[dict] = None,
    method: str = "GET",
    json_body: Optional[dict] = None,
    log: Optional[logging.Logger] = None,
    label: str = "",
) -> Optional[Any]:
    """Fetch a JSON endpoint and return the parsed payload, or ``None`` on failure.

    Most ATS APIs return a JSON object (dict); a few (Lever) return a
    top-level array. Caller knows the shape — we just hand back whatever
    ``resp.json()`` returned without coercing the type.

    Returns ``None`` (not an exception) for 404, network errors, or invalid
    JSON. ``label`` is a human-readable identifier (board slug, tenant
    name, etc.) used purely in log messages so the warning line points at
    the row that broke.

    Use ``method="POST"`` + ``json_body=...`` for endpoints that need
    POST (e.g. Workday's job-search). Default is GET.
    """
    try:
        if method.upper() == "POST":
            resp = requests.post(url, json=json_body, headers=headers, timeout=timeout)
        else:
            resp = requests.get(url, headers=headers, timeout=timeout)
        if resp.status_code == 404:
            if log is not None:
                log.warning("%s: 404 for %r — drop from list", log.name, label or url)
            return None
        resp.raise_for_status()
        parsed = resp.json()
        # Preserve the original type; only substitute an empty dict when the
        # parser returned ``None`` (rare — only on a literal "null" body).
        return parsed if parsed is not None else {}
    except Exception as exc:
        if log is not None:
            log.warning("%s: fetch failed for %r: %s", log.name, label or url, exc)
        return None


# Default polite-pause between consecutive requests against the same host.
# Sources that need a different cadence can pass an explicit value; the
# 0.5-second default matches the original inline ``time.sleep(0.5)`` calls.
DEFAULT_PAUSE_SECONDS = 0.5


def sleep_between_requests(seconds: float = DEFAULT_PAUSE_SECONDS) -> None:
    """Polite pause between consecutive requests to the same host.

    Wrapping ``time.sleep`` in a named helper makes the *why* explicit at
    the call site and gives us one place to add jitter / per-host
    throttling later if a source needs it.
    """
    time.sleep(seconds)
