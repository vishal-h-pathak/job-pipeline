"""scripts/detect_fill_drift.py — per-ATS fill-rate drift aggregator (Task 4).

Task 3 (feat/submit-fill-robustness) started writing a ``fill_report`` — one
entry per declared field spec, ``{key, label, type, required, attempted,
matched_selector, value_verified, misses}`` (see
``jobpipe/submit/adapters/prepare_dom/field_maps.py::apply_field_map``) —
into ``application_attempts.notes.fill_report`` on every pre-fill attempt,
clean or degraded. That's raw per-attempt evidence; nothing aggregated it
across attempts. When an ATS redesigns its form, every selector in
``field_maps.yml`` for that ATS starts silently missing, and the only signal
today is a human noticing a string of assisted-manual hand-offs. This module
closes that loop the same way ``jobpipe/tailor/scripts/analyze_patterns.py``
closes the resume/reply-rate loop: fetch -> aggregate -> decide -> report,
standalone-runnable.

Fill-rate definition
---------------------
For a window of attempts, pool every ``fill_report`` entry across those
attempt rows and compute::

    rate = (# entries with attempted=True and value_verified=True)
           / (# entries with attempted=True)

Only ``attempted`` entries are in the denominator — an optional field with
no value to try (``attempted=False``, e.g. no LinkedIn URL on file) isn't a
selector failure, so it must not dilute the rate the way "5 of 20 fields
touched" would.

Baseline definition (must be defensible, not a hardcoded constant)
--------------------------------------------------------------------
The **trailing baseline** for an ATS's most recent ``window`` (default
:data:`DEFAULT_WINDOW` = 10) attempts is the fill-rate over the **previous**
``window`` attempts immediately before it — i.e. attempts ``[window+1 .. 2*
window]`` counting back from the newest. Both windows are the same size, so
neither the "current" nor the "baseline" sample is a runt that swings the
comparison on a handful of rows, and the whole thing is a pure window-over-
window diff rather than an average pinned to some historical constant that
would need separate upkeep as an ATS's baseline naturally drifts (a Lever
redesign 8 months ago is not this week's baseline). This does mean an ATS
needs ``2 * window`` attempts of history before drift detection activates at
all — see "Sparse data" below.

Threshold: a **15 percentage-point absolute drop** (``current_rate -
baseline_rate <= -15pp``, i.e. :data:`DEFAULT_DROP_THRESHOLD_PP`). Absolute
pp rather than a relative drop because the metric is already a rate in
[0, 1] — a relative "20% worse" reads very differently at a 95% baseline
(down to 76%, mildly concerning) than at a 40% baseline (down to 32%, an ATS
that was already rough). 15pp was picked to sit well above ordinary
attempt-to-attempt noise (a single required custom question an ATS added
this week costs a few points, not fifteen) while catching the failure mode
this exists for — a redesign that breaks several selectors at once, which
historically shows up as a 30-50pp cliff (see the worked example below).

Sparse data (must be side-effect-free, never crash, never false-positive)
----------------------------------------------------------------------------
An ATS is skipped (logged, no notification) when:
  - it has fewer than ``window`` attempts total (no current window yet), or
  - it has fewer than ``window`` attempts *before* the current window (no
    baseline yet), or
  - either window's pooled ``attempted`` count is zero (nothing to compute a
    rate from — e.g. every attempt in that window never got a value to try).

Notification
------------
Reuses ``jobpipe.notify.create_notification`` (no new notify transport) with
the same synthetic-job pattern
``RealtimeSubscriptionGuard._notify_realtime_gap`` in
``jobpipe/submit/watch.py`` already established for a non-job-specific
infra alert: a job-shaped dict carrying only ``id=None`` / ``company`` /
``title`` so ``create_notification``'s job-oriented signature has something
to render without a real ``jobs`` row backing it.

Designed to run standalone (cron, or ad hoc)::

    python -m jobpipe.submit.scripts.detect_fill_drift

and to be imported and called directly from anywhere that wants a periodic
check (e.g. the submit watcher's end-of-cycle hook) without any of that
call site needing to know how the aggregation works — ``detect_fill_drift()``
is the one entry point.
"""

from __future__ import annotations

import argparse
import logging
from dataclasses import dataclass
from typing import Callable, Iterable, Optional

import jobpipe.db as db
from jobpipe.notify import create_notification

logger = logging.getLogger("detect_fill_drift")

# The ATS names the field-map system knows how to fill deterministically
# (jobpipe/submit/adapters/prepare_dom/field_maps.yml's top-level keys),
# plus "universal" — the no-ATS-map Claude tool-use fallback
# (adapters/prepare_dom/universal.py), which also writes a fill_report.
# Kept as an explicit tuple (matching analyze_patterns.py's
# DEFAULT_DIMENSIONS-style constant) rather than derived from the YAML at
# import time, since a fully-generic "whatever adapter values happen to be
# in the table" query would silently start tracking drift for typos/one-off
# adapter strings too.
KNOWN_ATS: tuple[str, ...] = ("greenhouse", "lever", "ashby", "universal")

# Trailing window size, each side of the comparison (current N vs. the N
# immediately before it). ~10 attempts is enough to smooth out one-off
# custom-question misses without requiring weeks of history before the
# check activates at all.
DEFAULT_WINDOW = 10

# Absolute percentage-point drop that counts as "meaningfully below
# baseline" — see the module docstring for the rationale.
DEFAULT_DROP_THRESHOLD_PP = 15.0


@dataclass
class WindowStats:
    n_attempts: int
    attempted: int
    verified: int
    # Count of attempts in this window whose ``notes.readiness_timeout``
    # was true (Task 3 / #4: the adapter proceeded without ever seeing
    # positive form-readiness evidence). Tracked SEPARATELY from the
    # attempted/verified fill-rate math below — a batch of readiness
    # timeouts (a slow analytics-heavy site that week) and a batch of
    # genuine selector misses (an ATS redesign) both drag the raw rate
    # down the same way, but they call for very different human
    # responses. This count rides alongside the rate as a diagnostic
    # signal rather than being excluded from it: a readiness timeout does
    # NOT stop the adapter from attempting the fill (Task 3's design), so
    # the fill_report entries from a timed-out attempt are still real
    # evidence — just evidence worth reading with this context attached.
    readiness_timeouts: int = 0

    @property
    def rate(self) -> Optional[float]:
        """``None`` (not 0.0) when nothing in this window was ever
        attempted — a rate of 0 would misleadingly read as "everything
        failed" rather than "no data.\""""
        return (self.verified / self.attempted) if self.attempted else None


def fetch_recent_attempts(
    ats: str, *, limit: int, before_id: Optional[int] = None,
) -> list[dict]:
    """Return up to ``limit`` ``application_attempts`` rows for ``ats``,
    newest first (ordered by ``id`` desc — a monotonically increasing PK,
    a stabler trailing-order key than ``started_at``, which two attempts
    opened in the same second could tie on).

    ``before_id`` pages further back in time (strictly older than that id)
    so the baseline window can be fetched as "the ``limit`` rows immediately
    before the current window" without re-fetching or double-counting rows
    the current-window query already returned.
    """
    query = (
        db._get_client()
        .table("application_attempts")
        .select("id, notes, started_at")
        .eq("adapter", ats)
    )
    if before_id is not None:
        query = query.lt("id", before_id)
    rows = query.order("id", desc=True).limit(limit).execute().data or []
    return rows


def compute_window_stats(rows: list[dict]) -> WindowStats:
    """Pool every ``fill_report`` entry across ``rows`` (attempt rows) into
    one attempted/verified count — see the module docstring's fill-rate
    definition. Rows with no ``notes.fill_report`` (e.g. an attempt that
    predates Task 3, or a row some other code path wrote without one)
    contribute nothing, not a crash.

    Also counts ``readiness_timeouts`` — the number of ROWS (not
    fill_report entries; this is a per-attempt flag, not per-field) whose
    ``notes.readiness_timeout`` is true.
    """
    attempted = 0
    verified = 0
    readiness_timeouts = 0
    for row in rows:
        notes = row.get("notes") or {}
        for entry in notes.get("fill_report") or []:
            if entry.get("attempted"):
                attempted += 1
                if entry.get("value_verified"):
                    verified += 1
        if notes.get("readiness_timeout"):
            readiness_timeouts += 1
    return WindowStats(
        n_attempts=len(rows), attempted=attempted, verified=verified,
        readiness_timeouts=readiness_timeouts,
    )


def _notify_drift(ats: str, current: WindowStats, baseline: WindowStats) -> str:
    """Build and send the drift alert; returns the message sent."""
    message = (
        f"{ats.capitalize()} fill rate {current.rate:.0%} over last "
        f"{current.n_attempts} attempts (baseline {baseline.rate:.0%}) "
        "— selectors likely drifted"
    )
    # Surface readiness-timeout attempts SEPARATELY from the fill-rate
    # numbers above — a batch of slow-page timeouts and a batch of genuine
    # selector misses both drag the same rate down, but a human deciding
    # whether to go fix field_maps.yml needs to know which one they're
    # looking at. Only appended when non-zero so the message stays
    # byte-identical to before this fix in the common (no timeouts) case.
    if current.readiness_timeouts:
        message += (
            f" ({current.readiness_timeouts}/{current.n_attempts} recent "
            "attempts had a readiness timeout — some of this drop may "
            "reflect slow page loads, not selector drift)"
        )
    # Synthetic "job" dict — same non-job-specific infra-notification
    # pattern as RealtimeSubscriptionGuard._notify_realtime_gap in
    # jobpipe/submit/watch.py (create_notification's signature expects a
    # job-shaped dict; there is no real jobs row backing an ATS-wide alert).
    create_notification(
        "failed",
        {"id": None, "company": "fill-drift-detector", "title": f"{ats} fill-rate drift"},
        message,
    )
    logger.warning(message)
    return message


def check_drift_for_ats(
    ats: str,
    *,
    window: int = DEFAULT_WINDOW,
    drop_threshold_pp: float = DEFAULT_DROP_THRESHOLD_PP,
    fetch: Callable[..., list[dict]] = fetch_recent_attempts,
) -> Optional[dict]:
    """Check one ATS for fill-rate drift. Returns a result dict (whether or
    not it notified) once there's enough history to compare, ``None`` when
    there isn't (sparse-data cases — logged, never raises, never notifies).
    """
    current_rows = fetch(ats, limit=window)
    if len(current_rows) < window:
        logger.info(
            "drift check: %s has only %d/%d attempt(s) — not enough history yet",
            ats, len(current_rows), window,
        )
        return None

    oldest_current_id = min(row["id"] for row in current_rows)
    baseline_rows = fetch(ats, limit=window, before_id=oldest_current_id)
    if len(baseline_rows) < window:
        logger.info(
            "drift check: %s has only %d/%d baseline attempt(s) — not enough "
            "trailing history yet",
            ats, len(baseline_rows), window,
        )
        return None

    current = compute_window_stats(current_rows)
    baseline = compute_window_stats(baseline_rows)
    if current.rate is None or baseline.rate is None:
        logger.info(
            "drift check: %s window has no attempted fill_report entries — skipping",
            ats,
        )
        return None

    drop_pp = (baseline.rate - current.rate) * 100
    result: dict = {
        "ats": ats,
        "current_rate": current.rate,
        "baseline_rate": baseline.rate,
        "drop_pp": round(drop_pp, 1),
        "n_current": current.n_attempts,
        "n_baseline": baseline.n_attempts,
        "current_readiness_timeouts": current.readiness_timeouts,
        "baseline_readiness_timeouts": baseline.readiness_timeouts,
        "notified": False,
    }
    if drop_pp >= drop_threshold_pp:
        result["notified"] = True
        result["message"] = _notify_drift(ats, current, baseline)
    return result


def detect_fill_drift(
    *,
    ats_list: Iterable[str] = KNOWN_ATS,
    window: int = DEFAULT_WINDOW,
    drop_threshold_pp: float = DEFAULT_DROP_THRESHOLD_PP,
    fetch: Callable[..., list[dict]] = fetch_recent_attempts,
) -> list[dict]:
    """Check every ATS in ``ats_list`` for fill-rate drift.

    The one entry point meant for other code to call (a cron script, or a
    future watcher end-of-cycle hook) — it never raises: a single ATS's
    check failing (a transient DB error) is logged and skipped rather than
    aborting the whole sweep. Returns the list of per-ATS results that had
    enough history to be evaluated (sparse ATSs are omitted, not returned
    as `None` entries).
    """
    results = []
    for ats in ats_list:
        try:
            result = check_drift_for_ats(
                ats, window=window, drop_threshold_pp=drop_threshold_pp, fetch=fetch,
            )
        except Exception:  # noqa: BLE001 — one ATS's failure must not sink the sweep
            logger.exception("drift check failed for %s", ats)
            continue
        if result is not None:
            results.append(result)
    return results


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Per-ATS fill-rate drift aggregator (Task 4)",
    )
    parser.add_argument(
        "--ats",
        default=",".join(KNOWN_ATS),
        help="Comma-separated ATS names to check. Default: %(default)s.",
    )
    parser.add_argument(
        "--window", type=int, default=DEFAULT_WINDOW,
        help="Trailing window size (attempts per side). Default: %(default)s.",
    )
    parser.add_argument(
        "--threshold", type=float, default=DEFAULT_DROP_THRESHOLD_PP,
        help="Absolute percentage-point drop that triggers a notification. "
             "Default: %(default)s.",
    )
    args = parser.parse_args()

    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(name)s — %(message)s",
    )
    ats_list = tuple(a.strip() for a in args.ats.split(",") if a.strip())

    results = detect_fill_drift(
        ats_list=ats_list, window=args.window, drop_threshold_pp=args.threshold,
    )
    if not results:
        logger.info("No ATS had enough history to evaluate this run.")
        return
    for r in results:
        flag = "DRIFT — notified" if r["notified"] else "ok"
        logger.info(
            "%-12s current=%.0f%% baseline=%.0f%% drop=%.1fpp "
            "readiness_timeouts=%d/%d [%s]",
            r["ats"], r["current_rate"] * 100, r["baseline_rate"] * 100,
            r["drop_pp"], r["current_readiness_timeouts"], r["n_current"], flag,
        )


if __name__ == "__main__":
    main()
