#!/usr/bin/env python3
"""scripts/mark_run.py — create/update a public.runs row from GitHub Actions.

Called by .github/workflows/hunt.yml and tailor.yml at three points per
run: at the top (status='running', stamps started_at), at the bottom on
success (status='completed', stamps ended_at, attaches GHA url + log),
and at the bottom on failure (status='failed', same stamps + log).

Dashboard-dispatched runs arrive with a pre-inserted row id (the
RunsPanel inserts at click time and passes it as the workflow's
`run_id` input). Cron-triggered runs have no such row, so hunt.yml's
schedule path calls `create` first: it inserts a row directly in
status='running' (triggered_by='cron') and prints the new id for the
workflow to thread into the closing update.

The dashboard's RunsPanel polls /api/dashboard/runs every 5s while any
visible row is pending or running, so these updates surface in the UI
within one polling tick.

Usage:
    python scripts/mark_run.py create <hunt|tailor|tailor_manual> [--triggered-by cron]
    python scripts/mark_run.py <run_id> running
    python scripts/mark_run.py <run_id> completed --log "...tail..." --gha-url "https://..."
    python scripts/mark_run.py <run_id> failed    --log "...tail..." --gha-url "https://..."

Connection reuse: imports jobpipe.db.service_client (the lazy module-
level singleton from PR-8) so we don't re-implement Supabase wiring.
"""

from __future__ import annotations

import argparse
import sys
from datetime import datetime, timezone

from jobpipe import db


def _utcnow() -> str:
    return datetime.now(timezone.utc).isoformat()


def _create(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(
        prog="mark_run.py create",
        description="Insert a public.runs row and print its id.",
    )
    parser.add_argument(
        "kind", choices=("hunt", "tailor", "tailor_manual"),
        help="Pipeline phase this run belongs to",
    )
    parser.add_argument(
        "--triggered-by", dest="triggered_by", default="cron",
        help="runs.triggered_by value (default: cron)",
    )
    args = parser.parse_args(argv)

    # Born running: by the time the workflow calls create, the job is
    # already executing, so there's no pending window to represent.
    payload = {
        "kind": args.kind,
        "status": "running",
        "triggered_by": args.triggered_by,
        "started_at": _utcnow(),
    }
    res = db.service_client.table("runs").insert(payload).execute()
    if not res.data:
        print("mark_run: insert returned no row", file=sys.stderr)
        return 1
    print(res.data[0]["id"])
    return 0


def main() -> int:
    if len(sys.argv) > 1 and sys.argv[1] == "create":
        return _create(sys.argv[2:])

    parser = argparse.ArgumentParser(description="Update a public.runs row.")
    parser.add_argument("run_id", help="public.runs.id (UUID) to update")
    parser.add_argument(
        "status", choices=("running", "completed", "failed"),
        help="New status for the run",
    )
    parser.add_argument("--log", default=None, help="Tail-of-log excerpt")
    parser.add_argument("--gha-url", dest="gha_url", default=None,
                        help="GitHub Actions run URL")
    args = parser.parse_args()

    payload: dict = {"status": args.status}
    if args.status == "running":
        payload["started_at"] = _utcnow()
    else:
        payload["ended_at"] = _utcnow()
    if args.log is not None:
        payload["log_excerpt"] = args.log
    if args.gha_url is not None:
        payload["github_run_url"] = args.gha_url

    res = (
        db.service_client.table("runs")
        .update(payload)
        .eq("id", args.run_id)
        .execute()
    )
    # Roll up BEFORE the res.data check: supabase-py's update().execute() can
    # return an empty representation (.data == []) even on a successful write,
    # so a completed run must fold its cost_events into runs.cost_usd here,
    # independent of the update's return shape. Best-effort — rollup_run
    # swallows its own errors and never raises.
    if args.status == "completed" and args.run_id:
        from jobpipe.shared import cost
        cost.rollup_run(args.run_id)

    if not res.data:
        print(f"mark_run: no row updated for id={args.run_id}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
