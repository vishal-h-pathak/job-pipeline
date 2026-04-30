#!/usr/bin/env python3
"""scripts/mark_run.py — update a public.runs row from inside GitHub Actions.

Called by .github/workflows/hunt.yml and tailor.yml at three points per
run: at the top (status='running', stamps started_at), at the bottom on
success (status='completed', stamps ended_at, attaches GHA url + log),
and at the bottom on failure (status='failed', same stamps + log).

The dashboard's RunsPanel polls /api/dashboard/runs every 5s while any
visible row is pending or running, so these updates surface in the UI
within one polling tick.

Usage:
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


def main() -> int:
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
    if not res.data:
        print(f"mark_run: no row updated for id={args.run_id}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
