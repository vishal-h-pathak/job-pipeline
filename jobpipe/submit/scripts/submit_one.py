"""
submit_one.py — Run a single submission attempt against a known job_id.

Primary debugging tool during Milestone 3+ bring-up. Bypasses the poll loop
and runs process_one() directly, optionally in a visible browser so you can
watch the adapter drive the form.

Usage:
    python scripts/submit_one.py --job-id <uuid>
    python scripts/submit_one.py --job-id <uuid> --headed
    python scripts/submit_one.py --job-id <uuid> --no-submit   # (future flag)

Stub: Milestone 3 adds the --no-submit flag by threading a SubmitMode through
SubmissionContext. For now the script plumbs argparse -> db.get_job -> main.process_one.
"""

from __future__ import annotations

import argparse
import asyncio
import logging
import os
import sys

# Make the package root importable when running as a plain script.
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import db                   # noqa: E402
from main import process_one  # noqa: E402

logger = logging.getLogger("submitter.submit_one")


def parse_args() -> argparse.Namespace:
    ap = argparse.ArgumentParser(description="Submit a single job by id.")
    ap.add_argument("--job-id", required=True, help="jobs.id to process")
    ap.add_argument("--headed", action="store_true",
                    help="Override HEADLESS=true so you can watch the browser.")
    ap.add_argument("--no-submit", action="store_true",
                    help="Fill the form but stop before confirm.click_submit_and_verify. (TBD Milestone 3)")
    return ap.parse_args()


async def main() -> None:
    args = parse_args()
    if args.headed:
        os.environ["HEADLESS"] = "false"

    job = db.get_job(args.job_id)
    if not job:
        print(f"no such job: {args.job_id}", file=sys.stderr)
        sys.exit(2)
    if args.no_submit:
        # TODO Milestone 3: plumb a SubmitMode.PREPARE through SubmissionContext.
        logger.warning("--no-submit is not yet implemented; proceeding normally.")

    await process_one(job)


if __name__ == "__main__":
    logging.basicConfig(
        level=logging.DEBUG,
        format="%(asctime)s %(name)s %(levelname)s %(message)s",
    )
    asyncio.run(main())
