"""
notify.py — Notification layer for the job-applicant agent.

Instead of external push services, notifications are written to a
Supabase 'notifications' table that the portfolio dashboard reads.
The dashboard at vishal.pa.thak.io serves as the single notification interface.
"""

import logging
from datetime import datetime, timezone

from config import SUPABASE_URL, SUPABASE_KEY

logger = logging.getLogger("notify")

# Lazy-init to avoid import-time Supabase connection
_client = None


def _get_client():
    global _client
    if _client is None:
        from supabase import create_client
        _client = create_client(SUPABASE_URL, SUPABASE_KEY)
    return _client


def create_notification(notification_type: str, job: dict, message: str = "") -> bool:
    """
    Write a notification record to Supabase for the dashboard to display.

    Args:
        notification_type: One of 'ready_for_review', 'applied', 'failed'
        job: The job record dict
        message: Optional additional context

    Returns:
        True if written successfully
    """
    try:
        client = _get_client()
        client.table("notifications").insert({
            "type": notification_type,
            "job_id": job.get("id"),
            "title": f"{job.get('company', 'Unknown')} — {job.get('title', 'Unknown')}",
            "message": message,
            "read": False,
            "created_at": datetime.now(timezone.utc).isoformat(),
        }).execute()
        logger.info(f"Notification created: {notification_type} for {job.get('company')}")
        return True
    except Exception as e:
        # Don't let notification failures break the pipeline.
        # If the notifications table doesn't exist yet, just log it.
        logger.warning(f"Could not write notification (table may not exist yet): {e}")
        return False


def notify_ready_for_review(job: dict) -> bool:
    """Notify that a job application is ready for human review."""
    message = (
        f"Score: {job.get('score', '?')}/10 | Tier: {job.get('tier', '?')}\n"
        f"{job.get('reasoning', '')}"
    )
    return create_notification("ready_for_review", job, message)


def notify_applied(job: dict) -> bool:
    """Notify that an application was submitted successfully."""
    return create_notification("applied", job, "Application submitted.")


def notify_failed(job: dict, reason: str) -> bool:
    """Notify that an application submission failed."""
    return create_notification("failed", job, f"Reason: {reason}")
