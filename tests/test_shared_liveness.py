"""tests/test_shared_liveness.py — jobpipe.shared.liveness.classify_posting.

The single source of truth for "is this posting dead?" — shared by the
weekly check_liveness cron and the hunt-time discovery gate so the two
can't drift. Conservative: only a POSITIVE dead signal (404/410, a
dead-URL redirect, or a closed-phrase match) returns "dead". Network
errors / timeouts / non-404 5xx return "unknown" (never drop).
"""

from __future__ import annotations

import pytest

from jobpipe.shared.liveness import classify_posting, DEAD_BODY_PHRASES


def test_404_is_dead():
    state, reason = classify_posting(status_code=404, html="<html>gone</html>")
    assert state == "dead"
    assert "404" in reason


def test_410_is_dead():
    state, _ = classify_posting(status_code=410, html="")
    assert state == "dead"


def test_closed_phrase_in_body_is_dead():
    html = "<h1>Senior Engineer</h1><p>This position is no longer available.</p>"
    state, reason = classify_posting(status_code=200, html=html)
    assert state == "dead"
    assert "phrase" in reason


@pytest.mark.parametrize("phrase", DEAD_BODY_PHRASES)
def test_every_known_dead_phrase_trips(phrase):
    state, _ = classify_posting(status_code=200, html=f"<p>{phrase.upper()}</p>")
    assert state == "dead", f"phrase not detected: {phrase!r}"


def test_dead_url_substring_is_dead():
    state, reason = classify_posting(
        status_code=200, html="<p>ok</p>",
        final_url="https://boards.greenhouse.io/acme/jobs/expired",
    )
    assert state == "dead"


def test_open_posting_is_live():
    html = "<h1>Engineer</h1><p>Apply now — we're hiring!</p>"
    state, reason = classify_posting(status_code=200, html=html)
    assert state == "live"
    assert reason == "alive"


def test_none_status_is_unknown():
    state, reason = classify_posting(status_code=None, html=None)
    assert state == "unknown"


def test_non_404_5xx_is_unknown_not_dead():
    """A 500/503 is transient — must never be treated as a positive dead
    signal (false-positive safety)."""
    for code in (500, 502, 503):
        state, _ = classify_posting(status_code=code, html="")
        assert state == "unknown", f"{code} should be unknown, got {state}"
