"""jobpipe-tailor-one CLI — manual job-URL tailor entry point.

Wired as ``jobpipe-tailor-one`` in pyproject.toml::[project.scripts]
in step ③.

    jobpipe-tailor-one <URL>
    jobpipe-tailor-one <URL> --run-id <uuid>     # GHA correlation
    jobpipe-tailor-one --status                  # delegates to print_status

Branches internally on confidence (Amendment 1):

    high → upsert status='approved'
         → process_one_approved_job(job_id) inline (materials in Storage)
         → print "job_id=… status=ready_for_review materials_url=…"

    low  → upsert status='discovered'
         → DO NOT tailor
         → print "job_id=… status=discovered review_url=/dashboard/review/{id}"

Exit codes:
    0 — success
    2 — unsupported URL (couldn't pick an ATS scraper)
    3 — scrape failed (HTTP / parse)
    4 — collision (existing row in unsafe state)
"""

from __future__ import annotations

import argparse
import logging
import sys
from typing import Optional

from . import ScrapeError, UnsupportedUrl
from .resolve import resolve_url
from .upsert import CollisionError, upsert_manual_job

logger = logging.getLogger("tailor.manual.cli")


def _review_url(job_id: str) -> str:
    """Relative path the dashboard composes with its own origin."""
    return f"/dashboard/review/{job_id}"


def _materials_url(job_id: str) -> str:
    return f"/dashboard/review/{job_id}"  # same surface; materials shown inline


def _tailor_one(job_id: str) -> str:
    """Call process_one_approved_job and return the row's final status."""
    # Lazy import: pulls in the LaTeX + LLM stack; only needed on the
    # high-confidence path.
    from jobpipe.db import get_job
    from jobpipe.tailor.pipeline import process_one_approved_job

    process_one_approved_job(job_id)
    row = get_job(job_id) or {}
    return (row.get("status") or "unknown").strip()


def run(argv: Optional[list[str]] = None) -> int:
    """Entry point. Returns the exit code so tests can call directly."""
    parser = argparse.ArgumentParser(
        prog="jobpipe-tailor-one",
        description=(
            "Manual job-URL tailor — paste a posting URL, get a tailored row "
            "(or a low-confidence row that lands in the dashboard review "
            "surface for human verification)."
        ),
    )
    parser.add_argument("url", nargs="?", help="Posting URL")
    parser.add_argument(
        "--run-id", metavar="UUID", default=None,
        help="dashboard runs.id (GHA correlation; step ③ persists this)",
    )
    parser.add_argument(
        "--status", action="store_true",
        help="Print job counts by status (delegates to pipeline.print_status)",
    )
    args = parser.parse_args(argv)

    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(name)s — %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )

    if args.status:
        from jobpipe.tailor.pipeline import print_status
        print_status()
        return 0

    if not args.url:
        parser.error("url is required (or pass --status)")
        return 2  # parser.error exits; included for tests that catch SystemExit

    try:
        posting = resolve_url(args.url)
    except UnsupportedUrl as exc:
        logger.error("unsupported URL: %s", exc)
        return 2
    except ScrapeError as exc:
        logger.error("scrape failed: %s", exc)
        return 3

    logger.info(
        "scraped: %r @ %s  (%s, confidence=%s)",
        posting.title, posting.company or "(unknown)",
        posting.ats_kind, posting.confidence,
    )

    try:
        job_id, final_status = upsert_manual_job(posting)
    except CollisionError as exc:
        logger.error(str(exc))
        return 4

    if final_status == "discovered":
        review = _review_url(job_id)
        logger.info(
            "low-confidence scrape — landed at status='discovered'; review URL: %s",
            review,
        )
        # Single line stdout so the GHA log + the runs.result writer in
        # step ③ can parse without ambiguity.
        print(f"job_id={job_id} status=discovered review_url={review}")
        return 0

    # High-confidence path.
    end_status = _tailor_one(job_id)
    print(
        f"job_id={job_id} status={end_status} "
        f"materials_url={_materials_url(job_id)}"
    )
    return 0


def main() -> None:  # pragma: no cover — thin SystemExit shim
    sys.exit(run())
