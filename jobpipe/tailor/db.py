"""
db.py — Supabase client for the job-applicant agent.

Reads approved jobs, writes status updates and application metadata.
"""

import logging
from datetime import datetime, timezone
from supabase import create_client
from config import SUPABASE_URL, SUPABASE_KEY

logger = logging.getLogger("db")

client = create_client(SUPABASE_URL, SUPABASE_KEY)


"""
Status lifecycle (aligned with existing portfolio dashboard values):
    new             — discovered by job-hunter (dashboard's default)
    ignored         — user dismissed in dashboard
    approved        — user approved for application
    preparing       — agent is tailoring resume + cover letter
    ready_to_submit — form filled, waiting for human review
    submit_confirmed — human greenlit submission
    applied         — application submitted
    failed          — submission error
"""


def get_jobs_by_status(status: str, limit: int = 10) -> list[dict]:
    """Fetch jobs with the given status, ordered by score descending."""
    result = (
        client.table("jobs")
        .select("*")
        .eq("status", status)
        .order("score", desc=True)
        .limit(limit)
        .execute()
    )
    return result.data


def get_approved_jobs(limit: int = 10) -> list[dict]:
    """Fetch jobs approved for application."""
    return get_jobs_by_status("approved", limit)


def get_confirmed_jobs(limit: int = 10) -> list[dict]:
    """Fetch jobs where user confirmed submission."""
    return get_jobs_by_status("submit_confirmed", limit)


def update_job_status(job_id: str, status: str, **extra_fields) -> dict:
    """
    Update a job's status and any additional fields.

    Args:
        job_id: The job's primary key.
        status: New status value.
        **extra_fields: Additional columns to update (e.g., resume_path, failure_reason).

    Returns:
        Updated row data.
    """
    data = {
        "status": status,
        "status_updated_at": datetime.now(timezone.utc).isoformat(),
        **extra_fields,
    }
    result = client.table("jobs").update(data).eq("id", job_id).execute()
    logger.info(f"Job {job_id} -> status={status}")
    return result.data


def mark_preparing(job_id: str) -> dict:
    return update_job_status(job_id, "preparing")


def mark_ready_to_submit(job_id: str, resume_path: str = None,
                          cover_letter_path: str = None,
                          application_url: str = None,
                          application_notes: str = None) -> dict:
    extras = {}
    if resume_path:
        extras["resume_path"] = resume_path
    if cover_letter_path:
        extras["cover_letter_path"] = cover_letter_path
    if application_url:
        extras["application_url"] = application_url
    if application_notes:
        extras["application_notes"] = application_notes
    return update_job_status(job_id, "ready_to_submit", **extras)


def mark_applied(job_id: str, application_notes: str = None) -> dict:
    extras = {"applied_at": datetime.now(timezone.utc).isoformat()}
    if application_notes:
        extras["application_notes"] = application_notes
    return update_job_status(job_id, "applied", **extras)


def mark_failed(job_id: str, reason: str) -> dict:
    return update_job_status(job_id, "failed", failure_reason=reason)


def get_job_counts_by_status() -> dict:
    """Get a count of jobs in each status for monitoring."""
    result = client.table("jobs").select("status").execute()
    counts = {}
    for row in result.data:
        s = row.get("status", "unknown")
        counts[s] = counts.get(s, 0) + 1
    return counts
