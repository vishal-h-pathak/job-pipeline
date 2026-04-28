"""scripts/check_liveness.py — Stale-posting liveness rechecker (J-8).

For every job whose status is `new` or `approved` and whose `created_at`
is older than 7 days, hit the original URL and check for 404 /
"no longer accepting applications" / "position closed" markers. If the
posting is positively dead, transition the row to `expired`.

Polite by design:
  - small jittered backoff between requests
  - per-host concurrency cap of 1
  - tight User-Agent that identifies the agent
  - timeouts on both connect and read
  - network errors leave the row alone (no false positives)

Run weekly via cron, e.g.:

    # nightly liveness recheck at 03:30 local time
    30 3 * * * cd ~/dev/jarvis/job-hunter && \
        /usr/local/bin/python -m scripts.check_liveness >> liveness.log 2>&1

The script writes structured log lines to `liveness.log` in the repo
root so the operator can grep for which postings died on which day.
"""

from __future__ import annotations

import argparse
import logging
import os
import random
import sys
import time
from collections import defaultdict
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Iterable
from urllib.parse import urlparse

import requests

_REPO_ROOT = Path(__file__).parent.parent
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

from dotenv import load_dotenv  # noqa: E402

load_dotenv()

from jobpipe.db import _client as get_client  # noqa: E402

logger = logging.getLogger("liveness")

USER_AGENT = "job-hunter-liveness/1.0 (+https://vishal.pa.thak.io)"
STALE_DAYS = 7
TIMEOUT_S = 12
PER_HOST_DELAY_RANGE_S = (1.0, 3.0)

# Phrases that, when present in a 200-OK page body, positively indicate
# the posting is closed. Conservative — we'd rather leave a row alive
# than misclassify a still-open role as expired.
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
)

DEAD_URL_SUBSTRINGS = (
    "job-not-found",
    "no-longer-available",
    "/expired",
)


def _eligible_jobs() -> list[dict]:
    """Pull jobs that are stale + still in early-funnel statuses."""
    cutoff = (datetime.now(timezone.utc) - timedelta(days=STALE_DAYS)).isoformat()
    client = get_client()
    rows = (
        client.table("jobs")
        .select("id, url, status, created_at")
        .in_("status", ["new", "approved"])
        .lt("created_at", cutoff)
        .execute()
        .data
        or []
    )
    return [r for r in rows if r.get("url")]


def _is_dead(resp: requests.Response | None) -> tuple[bool, str]:
    """Return (is_dead, reason). Network errors map to (False, '...')."""
    if resp is None:
        return False, "network-error"
    if resp.status_code == 404:
        return True, "404"
    if resp.status_code == 410:
        return True, "410-gone"
    final_url = (resp.url or "").lower()
    if any(s in final_url for s in DEAD_URL_SUBSTRINGS):
        return True, f"redirected-to:{final_url}"
    body = (resp.text or "").lower()
    for phrase in DEAD_BODY_PHRASES:
        if phrase in body:
            return True, f"phrase:{phrase!r}"
    return False, "alive"


def _check_url(url: str) -> tuple[bool, str]:
    """One liveness check. Returns (is_dead, reason)."""
    headers = {"User-Agent": USER_AGENT, "Accept": "text/html,*/*"}
    try:
        resp = requests.get(url, headers=headers, timeout=TIMEOUT_S, allow_redirects=True)
    except requests.RequestException as exc:
        logger.debug("net error on %s: %s", url, exc)
        return False, f"net-error:{exc.__class__.__name__}"
    return _is_dead(resp)


def _mark_expired(job_id: str, reason: str) -> None:
    client = get_client()
    client.table("jobs").update(
        {
            "status": "expired",
            "status_updated_at": datetime.now(timezone.utc).isoformat(),
            "failure_reason": f"liveness: {reason}",
        }
    ).eq("id", job_id).execute()


def run(dry_run: bool = False) -> dict:
    jobs = _eligible_jobs()
    logger.info("liveness check: %d eligible jobs (status in [new, approved], age > %dd)",
                len(jobs), STALE_DAYS)

    by_host: dict[str, list[dict]] = defaultdict(list)
    for j in jobs:
        host = urlparse(j["url"]).netloc.lower()
        by_host[host].append(j)

    counts = {"checked": 0, "dead": 0, "alive": 0, "errors": 0}

    # Round-robin across hosts so we never hammer one origin in a row.
    pending: list[Iterable[dict]] = [iter(rows) for rows in by_host.values()]
    while pending:
        next_pending: list[Iterable[dict]] = []
        for host_iter in pending:
            try:
                job = next(host_iter)
            except StopIteration:
                continue
            next_pending.append(host_iter)

            url = job["url"]
            is_dead, reason = _check_url(url)
            counts["checked"] += 1
            if reason.startswith("net-error"):
                counts["errors"] += 1
                logger.info("alive? unknown (%s) — %s — %s", reason, job["id"], url)
            elif is_dead:
                counts["dead"] += 1
                logger.info("DEAD (%s) — %s — %s", reason, job["id"], url)
                if not dry_run:
                    _mark_expired(job["id"], reason)
            else:
                counts["alive"] += 1
                logger.debug("alive — %s — %s", job["id"], url)

            time.sleep(random.uniform(*PER_HOST_DELAY_RANGE_S))
        pending = next_pending

    logger.info("liveness done: %s", counts)
    return counts


def main() -> None:
    parser = argparse.ArgumentParser(description="Stale-posting liveness rechecker (J-8)")
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Check URLs and log decisions but do not mutate jobs.status.",
    )
    parser.add_argument(
        "--log-file",
        default=str(_REPO_ROOT / "liveness.log"),
        help="Append structured log lines here (default: %(default)s).",
    )
    args = parser.parse_args()

    handlers = [logging.StreamHandler()]
    if args.log_file:
        handlers.append(logging.FileHandler(args.log_file))
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(name)s — %(message)s",
        handlers=handlers,
    )

    counts = run(dry_run=args.dry_run)
    print(
        "checked={checked} dead={dead} alive={alive} errors={errors}".format(**counts)
    )


if __name__ == "__main__":
    main()
