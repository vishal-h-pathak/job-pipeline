"""scripts/backfill_links.py — one-off aggregator-link backfill.

The direct-listing discovery gate (feat/hunt-direct-listings) resolves
aggregator links and checks openness at hunt time — but only for jobs
discovered AFTER it shipped. Rows already in the queue still carry raw
aggregator URLs with ``application_url IS NULL`` (notably the handful of
approved rows just reset for a tailor retry — the main reason to run this).

This script fixes those rows in place by reusing the very same hunt-gate
helper (``jobpipe.hunt.agent._resolve_link_and_liveness``, which runs
``jobpipe.shared.liveness.classify_posting`` internally), so the backfill
and the live hunt can't classify a posting differently.

Scope: ``status in {new, approved}``, url is an aggregator (NOT already a
direct ATS), ``application_url IS NULL``.

Per row:
  - resolve ``application_url`` + ``ats_kind`` + ``link_status``, OR
  - expire it (``status`` and ``link_status`` → ``expired``) on a POSITIVE
    dead signal (HTTP 404/410 or a closed-phrase match).

The suspicious-drop / skip gate is deliberately NOT applied — that gate
needs a fresh score, and this backfill spends ZERO LLM tokens. So an
approved row stays approved unless it is positively dead; an unresolvable
aggregator is merely flagged ``aggregator_unverified``, never skipped.

Idempotent (resolved rows no longer match the filter), no notify/digest,
no paid sources. DRY-RUN by default — prints what WOULD change. Pass
``--commit`` to write, since this touches live rows.

    python -m jobpipe.hunt.scripts.backfill_links            # dry run
    python -m jobpipe.hunt.scripts.backfill_links --commit   # apply
"""

from __future__ import annotations

import argparse
import logging
import random
import time
from dataclasses import dataclass

from dotenv import load_dotenv

load_dotenv()

from jobpipe.db import _get_client, _utcnow  # noqa: E402
# Reuse the live hunt gate (which calls classify_posting internally) so the
# backfill and the hunt agree on every resolve / dead decision.
from jobpipe.hunt.agent import _resolve_link_and_liveness  # noqa: E402
from jobpipe.tailor.url_resolver import is_ats_url  # noqa: E402

logger = logging.getLogger("hunt.backfill_links")

# Only passive / approved early-funnel rows. Active downstream statuses
# (preparing onward) are never touched.
BACKFILL_STATUSES = ("new", "approved")

# Polite spacing between resolver fetches (one HTTP GET per row).
PER_ROW_DELAY_RANGE_S = (1.0, 3.0)


@dataclass
class Change:
    id: str
    company: str
    url: str
    status: str
    action: str  # "resolve" | "expire"
    application_url: str | None = None
    ats_kind: str | None = None
    link_status: str | None = None
    reason: str = ""


def _is_candidate(row: dict) -> bool:
    """Aggregator-ish row that hasn't been resolved yet: ``application_url``
    empty AND the url isn't already a direct ATS link."""
    if row.get("application_url"):
        return False
    url = row.get("url") or ""
    if not url:
        return False
    return not is_ats_url(url)


def fetch_candidates(client, statuses=BACKFILL_STATUSES) -> list[dict]:
    """Rows eligible for backfill: status in ``statuses``, aggregator url,
    application_url NULL. The NULL/direct-ATS filtering happens in Python so
    the query stays a simple status ``IN`` (and re-runs stay idempotent —
    once a row has an application_url it no longer matches)."""
    rows = (
        client.table("jobs")
        .select("id, company, url, status, application_url")
        .in_("status", list(statuses))
        .execute()
        .data
        or []
    )
    return [r for r in rows if _is_candidate(r)]


def plan_change(row: dict) -> Change:
    """Resolve one row through the shared hunt-gate helper and decide the
    backfill action. Status-agnostic: resolve, or expire-if-dead. No
    suspicious gate (see module docstring)."""
    job = {"url": row.get("url") or ""}
    decision, _ = _resolve_link_and_liveness(job)
    common = dict(
        id=row.get("id"),
        company=row.get("company") or "?",
        url=row.get("url") or "",
        status=row.get("status") or "?",
    )
    if decision == "dead":
        return Change(
            action="expire",
            link_status="expired",
            reason=job.get("_liveness_reason", ""),
            **common,
        )
    return Change(
        action="resolve",
        application_url=job.get("application_url"),
        ats_kind=job.get("ats_kind"),
        link_status=job.get("link_status"),
        **common,
    )


def apply_change(client, change: Change) -> None:
    """Write one planned change. Resolve updates are status-preserving —
    only a positive dead signal moves the row to ``expired``."""
    if change.action == "expire":
        client.table("jobs").update(
            {
                "status": "expired",
                "link_status": "expired",
                "failure_reason": f"backfill liveness: {change.reason}",
                "status_updated_at": _utcnow(),
            }
        ).eq("id", change.id).execute()
    else:
        client.table("jobs").update(
            {
                "application_url": change.application_url,
                "ats_kind": change.ats_kind,
                "link_status": change.link_status,
            }
        ).eq("id", change.id).execute()


def _polite_sleep() -> None:
    time.sleep(random.uniform(*PER_ROW_DELAY_RANGE_S))


def _print_row(c: Change, n: int, total: int, written: bool) -> None:
    """Print one resolved row as it's processed (id, company, old url →
    resolved application_url, link_status)."""
    company = (c.company or "?")[:24]
    tag = "wrote" if written else "plan "
    if c.action == "expire":
        print(f"[{n}/{total}] {tag} [EXPIRE]  {c.id}  {company:24}  {c.status:9}")
        print(f"          {c.url}")
        print(f"          → dead signal: {c.reason}")
    else:
        print(f"[{n}/{total}] {tag} [{c.link_status or '?':21}]  {c.id}  "
              f"{company:24}  {c.status:9}")
        print(f"          {c.url}")
        print(f"          → {c.application_url}  (ats={c.ats_kind})")


def run(commit: bool, client=None, statuses=BACKFILL_STATUSES) -> dict:
    """Resolve each candidate, printing it as it goes; write per row when
    ``commit``. Returns counts."""
    client = client or _get_client()
    rows = fetch_candidates(client, statuses)

    counts = {"candidates": len(rows), "resolved": 0, "expired": 0, "written": 0}
    if not rows:
        print(f"no candidate rows (status {'/'.join(statuses)}, aggregator "
              "url, application_url NULL)")
        return counts

    print(f"{len(rows)} candidate row(s) [{', '.join(statuses)}]"
          f"{'' if commit else ' — DRY RUN, no writes'}:\n")

    total = len(rows)
    for i, row in enumerate(rows):
        change = plan_change(row)
        counts["resolved" if change.action == "resolve" else "expired"] += 1

        written = False
        if commit:
            try:
                apply_change(client, change)
                counts["written"] += 1
                written = True
            except Exception as e:  # noqa: BLE001 — one bad row shouldn't abort the rest
                logger.error("write failed for %s: %s", change.id, e)

        _print_row(change, i + 1, total, written)
        if i < total - 1:
            _polite_sleep()  # one HTTP GET per row — space them out

    if commit:
        print(f"\nwrote {counts['written']}/{total} row(s): "
              f"resolved={counts['resolved']} expired={counts['expired']}")
    else:
        print(f"\nDRY RUN — no rows written ({counts['resolved']} resolve, "
              f"{counts['expired']} expire). Re-run with --commit to apply.")
    return counts


def main() -> None:
    parser = argparse.ArgumentParser(
        prog="backfill_links",
        description="One-off: resolve aggregator links + expire dead rows "
                    "for already-queued jobs (status new/approved). DRY-RUN "
                    "unless --commit.",
    )
    parser.add_argument(
        "--commit",
        action="store_true",
        help="Write the changes. Without this flag the script only prints "
             "what would change (it touches live rows).",
    )
    parser.add_argument(
        "--status",
        choices=("new", "approved", "both"),
        default="both",
        help="Which status bucket to backfill (default: both). "
             "'approved' is the small, high-value slice; 'new' is the large "
             "discovery backlog.",
    )
    args = parser.parse_args()

    statuses = BACKFILL_STATUSES if args.status == "both" else (args.status,)

    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(name)s — %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )
    run(commit=args.commit, statuses=statuses)


if __name__ == "__main__":
    main()
