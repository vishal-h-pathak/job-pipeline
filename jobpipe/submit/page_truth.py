"""page_truth.py — Path-A post-submit browser-truth probes (P0 #1).

Nothing in the live pre-fill path (``tailor/pipeline.py::process_prefill_requested_jobs``)
ever looked at the page again after a human clicked Submit on the ATS — the
row went ``applied`` purely on the human's say-so. The retired Browserbase
Path B (``jobpipe/submit/confirm.py``) had exactly the signal-detection logic
this needed; this module ports the deterministic half of it (URL/text success
needles) into Path A and adds a generic validation-error probe, so
``_wait_for_human_decision`` can attach real evidence to the human's decision
instead of trusting it blindly.

Every function here is synchronous (Path A is sync Playwright, unlike Path B's
async Stagehand) and best-effort: any single DOM read that raises is
swallowed so a flaky probe never breaks the wait loop or blocks the human
from advancing.
"""

from __future__ import annotations

import logging
from typing import Any, Optional

logger = logging.getLogger("submit.page_truth")

# Ported verbatim from the retired jobpipe/submit/confirm.py:76-91 (Path B).
# Each entry is (url_needles, text_needles); either kind matching is enough
# to treat a submit as confirmed.
SUCCESS_SIGNALS: dict[str, tuple[tuple[str, ...], tuple[str, ...]]] = {
    "greenhouse": (
        ("/applications/thank_you", "thank-you", "thanks-for-applying"),
        (
            "Thanks for applying", "Application submitted",
            "We’ve received your application",
            "We've received your application",
        ),
    ),
    "lever": (
        ("/thanks", "/thank-you", "application-submitted"),
        ("Thanks for your application", "Application received"),
    ),
    "ashby": (
        ("/thanks", "application-submitted", "thank_you"),
        (
            "Thanks for applying", "Your application has been submitted",
            "Application received",
        ),
    ),
}

# Generic, ATS-agnostic validation-error markers — fires regardless of
# ats_kind, since a still-broken form looks similar across Greenhouse /
# Lever / Ashby / Workday / generic forms.
_ERROR_SELECTORS: tuple[str, ...] = (
    '[role="alert"]',
    '[aria-invalid="true"]',
    ".error",
    ".field-error",
    ".form-error",
    ".validation-error",
    ".error-message",
    ".input-error",
)

# Only consulted when a matched element's own text doesn't already read as
# an obvious error banner (kept for future extension; the element-based scan
# above is the primary signal).
_ERROR_TEXT_NEEDLES: tuple[str, ...] = (
    "This field is required",
    "is required",
    "Please fill",
    "Please complete",
)


def probe_success_signal(page: Any, ats_kind: str) -> Optional[dict]:
    """Return ``{"kind": "url_redirect"|"page_text", "detail": ...}`` if a
    deterministic post-submit success needle fires for ``ats_kind``, else
    ``None``. Mirrors ``confirm.py::_probe_url_and_text`` but sync and
    without the Stagehand session wrapper.
    """
    url_needles, text_needles = SUCCESS_SIGNALS.get(ats_kind, ((), ()))

    try:
        current_url = page.url
    except Exception:
        current_url = ""
    for needle in url_needles:
        if needle and needle in current_url:
            return {"kind": "url_redirect", "detail": current_url}

    if text_needles:
        try:
            text = page.content()[:50_000]
        except Exception:
            text = ""
        for needle in text_needles:
            if needle and needle in text:
                return {"kind": "page_text", "detail": needle}

    return None


def probe_error_signals(page: Any, *, log: logging.Logger | None = None) -> list[str]:
    """Return visible validation-error text snippets currently on the page.

    Best-effort DOM scan across the common ATS error markers
    (``role=alert``, ``aria-invalid``, ``.error``-ish classes). A single
    selector's failure (bad locator, detached node) is swallowed so the
    scan degrades to whatever it could read rather than raising into the
    wait loop.
    """
    log = log or logger
    found: list[str] = []
    for selector in _ERROR_SELECTORS:
        try:
            loc = page.locator(selector)
            count = loc.count()
        except Exception:
            continue
        # Cap per-selector reads — this runs on every poll tick while the
        # tab is open, so it must stay cheap even on a form with many
        # decorative ``.error`` classes.
        for i in range(min(count, 5)):
            try:
                el = loc.nth(i)
                if not el.is_visible(timeout=500):
                    continue
                text = (el.text_content() or "").strip()
            except Exception:
                continue
            if text and text not in found:
                found.append(text)
    return found


def capture_truth(page: Any, ats_kind: str, *, log: logging.Logger | None = None) -> dict:
    """Best-effort final-state snapshot: current URL, success signal (if
    any), and visible validation-error text.

    Screenshot bytes are captured and uploaded by the caller (pipeline.py
    owns the Storage round-trip) — this module stays DB/Storage-free so it
    is trivially unit-testable against a fake page.
    """
    log = log or logger
    try:
        final_url = page.url
    except Exception:
        final_url = None
    return {
        "final_url": final_url,
        "success_signal": probe_success_signal(page, ats_kind),
        "error_signals": probe_error_signals(page, log=log),
    }
