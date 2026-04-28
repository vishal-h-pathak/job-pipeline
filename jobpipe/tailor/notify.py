"""notify.py — Notification layer for the job-applicant agent (M-8).

Instead of external push services, notifications are written to a
Supabase 'notifications' table that the portfolio dashboard reads.
The dashboard at vishal.pa.thak.io serves as the single notification
interface; each notification's `message` field carries a deep link to
the cockpit (`/dashboard/review/{job_id}`) so a click takes the user
directly to the action they need to take.
"""

import logging
import os
from datetime import datetime, timezone
from typing import Union

from config import SUPABASE_URL, SUPABASE_KEY

logger = logging.getLogger("notify")

# Cockpit base URL. Override via PORTFOLIO_BASE_URL for staging /
# preview deploys; defaults to the production domain.
PORTFOLIO_BASE_URL = os.getenv(
    "PORTFOLIO_BASE_URL", "https://vishal.pa.thak.io"
).rstrip("/")


def cockpit_url(job_id: Union[str, int]) -> str:
    return f"{PORTFOLIO_BASE_URL}/dashboard/review/{job_id}"


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
    """Notify that a job application is ready for human review (M-8).

    Body now includes score, tier, archetype, and legitimacy alongside
    the cockpit deep link so the dashboard panel renders enough context
    to triage at a glance.
    """
    parts = [
        f"Score: {job.get('score', '?')}/10",
        f"Tier: {job.get('tier', '?')}",
    ]
    if job.get("archetype"):
        parts.append(f"Archetype: {job['archetype']}")
    if job.get("legitimacy"):
        parts.append(f"Legitimacy: {job['legitimacy']}")
    header = " | ".join(parts)

    reasoning = (job.get("reasoning") or "").strip()
    body_lines = [header]
    if reasoning:
        body_lines.append(reasoning)
    body_lines.append(f"Cockpit: {cockpit_url(job.get('id'))}")
    return create_notification(
        "ready_for_review", job, "\n".join(body_lines)
    )


def notify_awaiting_submit(job: dict, screenshot_path: str = None) -> bool:
    """Notify that the form has been pre-filled and is awaiting the human's
    review and Submit click in the visible browser (M-5/M-8).

    Subject prefix uses [ACTION] so the dashboard notification panel
    surfaces this in the hot-path stack. Body includes the cockpit deep
    link and a reference to the post-fill screenshot the cockpit
    renders inline.
    """
    company = job.get("company", "Unknown")
    title = job.get("title", "Unknown")
    body_lines = [
        f"[ACTION] Form pre-filled for {company} - {title} - review and submit.",
        "Browser is open in your local terminal session. Review what was "
        "typed, fix anything wrong, click Submit yourself, then come back "
        "to the dashboard cockpit and click 'Mark Applied'.",
        f"Cockpit: {cockpit_url(job.get('id'))}",
    ]
    if screenshot_path:
        body_lines.append(f"Pre-fill screenshot: {screenshot_path}")
    return create_notification(
        "awaiting_human_submit", job, "\n".join(body_lines)
    )


def notify_applied(job: dict) -> bool:
    """Notify that an application was submitted successfully."""
    return create_notification("applied", job, "Application submitted.")


def notify_failed(job: dict, reason: str) -> bool:
    """Notify that an application submission failed."""
    return create_notification("failed", job, f"Reason: {reason}")
