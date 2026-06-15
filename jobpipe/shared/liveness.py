"""jobpipe.shared.liveness — positive dead-posting detection.

Single source of truth for "is this job posting dead?", shared by:

  - the weekly ``jobpipe/hunt/scripts/check_liveness.py`` cron (rechecks
    stale rows), and
  - the hunt-time discovery gate in ``jobpipe.hunt.agent._execute`` (drops
    closed postings before they reach the scorer / digest).

Keeping the closed-phrase list and the classification logic here means the
cron and the live hunt can't drift.

Conservative by design — only a POSITIVE signal returns ``"dead"``:
HTTP 404/410, a dead-URL redirect substring, or a closed-phrase match in
the page body. Network errors, timeouts, ``None`` status, and non-404 5xx
responses return ``"unknown"`` so a flaky fetch never buries a live role.
"""

from __future__ import annotations

# Phrases that, in a 200-OK page body, positively indicate the posting is
# closed. Conservative — we'd rather leave a row alive than misclassify a
# still-open role as expired.
DEAD_BODY_PHRASES = (
    "no longer accepting applications",
    "this position is no longer available",
    "this position is closed",
    "this job has expired",
    "we are no longer accepting applications",
    "position has been filled",
    "this opportunity has closed",
    "this role is no longer open",
    "this listing has ended",
    "job is no longer available",
    "position has been closed",
)

# Substrings in a final (post-redirect) URL that mean the posting is gone.
DEAD_URL_SUBSTRINGS = (
    "job-not-found",
    "no-longer-available",
    "/expired",
)


def classify_posting(
    *,
    status_code: int | None,
    html: str | None,
    final_url: str | None = None,
) -> tuple[str, str]:
    """Classify a fetched posting.

    Returns ``(state, reason)`` where ``state`` is one of ``"live"``,
    ``"dead"``, or ``"unknown"``. ``"dead"`` is returned ONLY on a positive
    signal:

      - HTTP 404 or 410,
      - a ``DEAD_URL_SUBSTRINGS`` match in ``final_url``, or
      - a ``DEAD_BODY_PHRASES`` match in ``html``.

    ``None`` status (network error / timeout) and non-404 5xx responses
    return ``"unknown"`` — never ``"dead"`` — so transient failures don't
    drop a live posting. Everything else is ``"live"``.
    """
    if status_code is None:
        return "unknown", "network-error"
    if status_code == 404:
        return "dead", "404"
    if status_code == 410:
        return "dead", "410-gone"
    # Transient server-side failure — not a positive dead signal.
    if status_code >= 500:
        return "unknown", f"http-{status_code}"

    final = (final_url or "").lower()
    for sub in DEAD_URL_SUBSTRINGS:
        if sub in final:
            return "dead", f"redirected-to:{final}"

    body = (html or "").lower()
    for phrase in DEAD_BODY_PHRASES:
        if phrase in body:
            return "dead", f"phrase:{phrase!r}"

    return "live", "alive"
