"""
main.py — Job Applicant Agent entry point.

Polls Supabase for approved jobs, tailors application materials,
fills forms via Playwright, and pauses for human approval before submission.

Usage:
    python main.py              # Run the polling loop
    python main.py --once       # Run one cycle and exit
    python main.py --status     # Print current job counts by status
"""

import sys
import time
import logging
import argparse
from datetime import datetime

from config import POLL_INTERVAL_MINUTES, HUMAN_APPROVAL_REQUIRED
from db import (
    get_approved_jobs,
    get_confirmed_jobs,
    mark_preparing,
    mark_ready_to_submit,
    mark_applied,
    mark_failed,
    get_job_counts_by_status,
)
from tailor.resume import tailor_resume
from tailor.cover_letter import generate_cover_letter
from applicant.detector import detect_ats, get_applicant
from notify import notify_ready_for_review, notify_applied, notify_failed

# ── Logging ──────────────────────────────────────────────────────────────────
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s — %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
logger = logging.getLogger("main")


def process_approved_jobs():
    """
    Phase 1: Pick up approved jobs, tailor materials, fill forms,
    then pause at ready_to_submit for human review.
    """
    jobs = get_approved_jobs()
    if not jobs:
        return

    logger.info(f"Found {len(jobs)} approved job(s) to process")

    for job in jobs:
        job_id = job["id"]
        company = job.get("company", "Unknown")
        title = job.get("title", "Unknown")
        url = job.get("url", "")

        logger.info(f"Processing: {company} — {title}")

        # ── Check ATS type ───────────────────────────────────────────────
        ats = detect_ats(url)
        if ats == "linkedin":
            logger.info(f"LinkedIn detected — flagging for manual application")
            mark_ready_to_submit(
                job_id,
                application_notes="LinkedIn: human-only application required",
            )
            notify_ready_for_review(job)
            continue

        # ── Mark as preparing ────────────────────────────────────────────
        mark_preparing(job_id)

        try:
            # ── Tailor resume ────────────────────────────────────────────
            logger.info(f"Tailoring resume for {company}...")
            resume_result = tailor_resume(job)

            # ── Generate cover letter ────────────────────────────────────
            logger.info(f"Generating cover letter for {company}...")
            cover_result = generate_cover_letter(job, resume_result)

            # ── Fill application form ────────────────────────────────────
            applicant = get_applicant(url)
            application_notes = f"ATS: {ats}\n"

            if applicant:
                logger.info(f"Filling application form via {ats} applicant...")
                fill_result = applicant.apply(
                    job,
                    resume_path=resume_result.get("output_path"),
                    cover_letter_path=cover_result.get("output_path"),
                )
                if fill_result.get("success"):
                    application_notes += f"Form filled successfully.\n{fill_result.get('notes', '')}"
                else:
                    application_notes += f"Form fill failed: {fill_result.get('notes', 'unknown error')}"
            else:
                application_notes += f"No automated applicant for {ats}. Manual form fill needed."

            # ── Mark ready for review ────────────────────────────────────
            mark_ready_to_submit(
                job_id,
                resume_path=resume_result.get("output_path"),
                cover_letter_path=cover_result.get("output_path"),
                application_url=url,
                application_notes=application_notes,
            )

            # ── Notify user ──────────────────────────────────────────────
            notify_ready_for_review(job)
            logger.info(f"Ready for review: {company} — {title}")

        except Exception as e:
            logger.error(f"Failed to process {company} — {title}: {e}")
            mark_failed(job_id, str(e))
            notify_failed(job, str(e))


def process_confirmed_jobs():
    """
    Phase 2: Submit applications that the user has confirmed.
    """
    jobs = get_confirmed_jobs()
    if not jobs:
        return

    logger.info(f"Found {len(jobs)} confirmed job(s) to submit")

    for job in jobs:
        job_id = job["id"]
        company = job.get("company", "Unknown")
        title = job.get("title", "Unknown")
        url = job.get("application_url") or job.get("url", "")

        logger.info(f"Submitting: {company} — {title}")

        try:
            applicant = get_applicant(url)
            if applicant:
                result = applicant.submit(job)
                if result.get("success"):
                    mark_applied(job_id, application_notes=result.get("notes"))
                    notify_applied(job)
                else:
                    mark_failed(job_id, result.get("notes", "submission failed"))
                    notify_failed(job, result.get("notes", "submission failed"))
            else:
                # No automated applicant — user confirmed manual application
                mark_applied(job_id, application_notes="Applied manually (no automated applicant)")
                notify_applied(job)

        except Exception as e:
            logger.error(f"Submission failed for {company}: {e}")
            mark_failed(job_id, str(e))
            notify_failed(job, str(e))


def run_cycle():
    """Run one complete poll cycle."""
    logger.info(f"=== Cycle at {datetime.utcnow().isoformat()} ===")
    process_approved_jobs()
    process_confirmed_jobs()


def print_status():
    """Print current job counts by status."""
    counts = get_job_counts_by_status()
    print("\nJob Status Summary:")
    print("-" * 35)
    for status, count in sorted(counts.items()):
        print(f"  {status:20s} {count:>5d}")
    print(f"  {'TOTAL':20s} {sum(counts.values()):>5d}")
    print()


def main():
    parser = argparse.ArgumentParser(description="Job Applicant Agent")
    parser.add_argument("--once", action="store_true", help="Run one cycle and exit")
    parser.add_argument("--status", action="store_true", help="Print job counts by status")
    args = parser.parse_args()

    if args.status:
        print_status()
        return

    print(f"""
╔═══════════════════════════════════════════════╗
║       JOB APPLICANT AGENT                     ║
║  {datetime.utcnow().strftime('%Y-%m-%d %H:%M:%S')} UTC{' ' * 21}║
║  Poll interval: {POLL_INTERVAL_MINUTES} min{' ' * 24}║
║  Human approval: {'ON' if HUMAN_APPROVAL_REQUIRED else 'OFF'}{' ' * 24}║
╚═══════════════════════════════════════════════╝
""")

    if args.once:
        run_cycle()
        return

    logger.info(f"Starting polling loop (every {POLL_INTERVAL_MINUTES} minutes)")

    while True:
        try:
            run_cycle()
        except Exception as e:
            logger.error(f"Cycle error: {e}")

        logger.info(f"Sleeping {POLL_INTERVAL_MINUTES} minutes...")
        time.sleep(POLL_INTERVAL_MINUTES * 60)


if __name__ == "__main__":
    main()
