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
Status lifecycle (M-2, career-ops alignment).

Canonical flow:
    discovered (alias: new) — discovered by job-hunter
    ignored                 — user dismissed in dashboard
    approved                — user approved for application
    preparing               — tailoring resume + cover letter + form_answers
    ready_for_review        — materials ready; awaiting "Pre-fill Form" click
    prefilling              — per-ATS DOM handler (or vision agent) running
    awaiting_human_submit   — form pre-filled in visible browser; user must
                              review and click Submit themselves, then
                              click "Mark Applied" in the dashboard
    applied                 — HUMAN clicked Mark Applied (source of truth)
    failed                  — pre-fill or submission error
    skipped                 — user opted out of this row
    expired                 — posting taken down (J-8)

Legacy statuses (ready_to_submit / submit_confirmed / submitting) were
collapsed into ready_for_review by migration 007. The CHECK constraint
on jobs.status enforces the new enum.

The system NEVER auto-sets status='applied'. Only the dashboard cockpit's
"Mark Applied" PATCH does — that click is the single source of truth for
whether a job actually got submitted.
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


def get_prefill_requested_jobs(limit: int = 10) -> list[dict]:
    """Fetch jobs the user clicked "Pre-fill Form" on (status='prefilling').

    The polling loop dispatches one of these at a time to the per-ATS
    DOM handler (or the prepare-only vision agent fallback). M-7 is
    the call site.
    """
    return get_jobs_by_status("prefilling", limit)


def get_confirmed_jobs(limit: int = 10) -> list[dict]:
    """DEPRECATED — `submit_confirmed` no longer exists under the M-2 CHECK
    enum. Returns []. Kept so legacy callers (`process_confirmed_jobs`,
    removed in M-7) don't crash on import. Use
    `get_prefill_requested_jobs()` instead.
    """
    logger.warning(
        "get_confirmed_jobs() is deprecated; status 'submit_confirmed' "
        "was removed in M-2. Use get_prefill_requested_jobs(). Returning []."
    )
    return []


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


def mark_ready_for_review(job_id: str, resume_path: str = None,
                          cover_letter_path: str = None,
                          application_url: str = None,
                          application_notes: str = None,
                          resume_pdf_path: str = None,
                          cover_letter_pdf_path: str = None,
                          archetype: str = None,
                          archetype_confidence: float = None,
                          submission_url: str = None) -> dict:
    """Mark a job ready for human review in the cockpit (M-2/M-6).

    Args:
        resume_path: Tailoring-metadata JSON blob (for dashboard display).
        cover_letter_path: Plain cover letter text (for form pasting).
        application_url: Resolved ATS URL the prefiller will navigate to.
        resume_pdf_path: Supabase Storage object key for the rendered resume PDF.
        cover_letter_pdf_path: Supabase Storage object key for cover letter PDF.
        archetype: Chosen archetype key (J-4) — persists for /dashboard/insights.
        archetype_confidence: Classifier confidence 0.0-1.0.
        submission_url: Real ATS apply URL post-resolution (M-3 column).
            Defaults to application_url if not supplied so callers that
            haven't been updated still get a value.
    """
    extras = {}
    if resume_path:
        extras["resume_path"] = resume_path
    if cover_letter_path:
        extras["cover_letter_path"] = cover_letter_path
    if application_url:
        extras["application_url"] = application_url
    if application_notes:
        extras["application_notes"] = application_notes
    if resume_pdf_path:
        extras["resume_pdf_path"] = resume_pdf_path
    if cover_letter_pdf_path:
        extras["cover_letter_pdf_path"] = cover_letter_pdf_path
    if archetype:
        extras["archetype"] = archetype
    if archetype_confidence is not None:
        extras["archetype_confidence"] = archetype_confidence
    sub_url = submission_url or application_url
    if sub_url:
        extras["submission_url"] = sub_url
    return update_job_status(job_id, "ready_for_review", **extras)


def mark_ready_to_submit(*args, **kwargs) -> dict:
    """DEPRECATED alias for `mark_ready_for_review` (M-2). Forwards all
    arguments unchanged. Logged once per call so the sweep to update
    callers is visible. Remove after every caller migrates."""
    logger.warning(
        "mark_ready_to_submit() is deprecated; use mark_ready_for_review() "
        "instead. Forwarding..."
    )
    return mark_ready_for_review(*args, **kwargs)


def mark_prefilling(job_id: str) -> dict:
    """User clicked "Pre-fill Form" in the cockpit. The polling loop
    picks the row up next cycle and dispatches it to the per-ATS handler
    (or vision-agent fallback)."""
    return update_job_status(job_id, "prefilling")


def mark_awaiting_submit(job_id: str, screenshot_path: str = None) -> dict:
    """Per-ATS handler finished filling the form. Browser stays open in
    the user's view; they review, click Submit themselves, then come
    back to the cockpit and click "Mark Applied".

    Args:
        screenshot_path: Supabase Storage key for the post-prefill
            screenshot the cockpit renders. Persisted to
            `prefill_screenshot_path` (M-3 column).
    """
    extras = {
        "prefill_completed_at": datetime.now(timezone.utc).isoformat(),
    }
    if screenshot_path:
        extras["prefill_screenshot_path"] = screenshot_path
    return update_job_status(job_id, "awaiting_human_submit", **extras)


def mark_skipped(job_id: str, reason: str = None) -> dict:
    """User opted out of submitting this row from the cockpit."""
    extras = {}
    if reason:
        extras["application_notes"] = reason
    return update_job_status(job_id, "skipped", **extras)


def mark_applied(job_id: str, application_notes: str = None,
                 submission_notes: str = None,
                 clear_materials: bool = True) -> dict:
    """Mark a job as applied — ALWAYS the result of a human click on the
    cockpit's "Mark Applied" button. Stamps both `applied_at` (legacy)
    and `submitted_at` (M-3) so analytics can rely on either.

    Args:
        application_notes: Free-text notes for the legacy column. Existing
            callers (process_confirmed_jobs, submit_one_visible — both
            removed in M-7) still write here.
        submission_notes: Free-text notes the human added in the cockpit
            modal. Persisted to the M-3 `submission_notes` column. Read
            by analytics + insights.
        clear_materials: When True (default), also deletes the generated
            PDFs from Supabase Storage and nulls the storage-path
            columns on the row.
    """
    now = datetime.now(timezone.utc).isoformat()
    extras = {"applied_at": now, "submitted_at": now}
    if application_notes:
        extras["application_notes"] = application_notes
    if submission_notes:
        extras["submission_notes"] = submission_notes
    if clear_materials:
        # Deferred import so this module stays importable when storage can't
        # initialize (e.g. missing service role key during tests).
        try:
            from storage import delete_all_for_job
            delete_all_for_job(job_id)
        except Exception as e:
            logger.warning(f"Could not clear materials for job {job_id}: {e}")
        extras["resume_pdf_path"] = None
        extras["cover_letter_pdf_path"] = None
    return update_job_status(job_id, "applied", **extras)


def delete_job_materials(job_id: str) -> None:
    """Delete generated PDFs from Storage and null the path columns on the row."""
    try:
        from storage import delete_all_for_job
        delete_all_for_job(job_id)
    except Exception as e:
        logger.warning(f"Storage delete failed for job {job_id}: {e}")
    client.table("jobs").update({
        "resume_pdf_path": None,
        "cover_letter_pdf_path": None,
    }).eq("id", job_id).execute()


def mark_tailor_failed(job_id: str, reason: str, *,
                       clear_materials: bool = True,
                       screenshot_path: str = None,
                       uncertain_fields: list = None) -> dict:
    """Tailor-side failure transition (PR-6 split from `mark_failed`).

    Use for failures that originate in the tailor pipeline: LaTeX compile
    error, prompt failure, missing inputs at tailoring time. Submitter
    failures use `jobpipe.submit.db.mark_failed` instead — that path
    requires an `application_attempts` row first per the design rule in
    JOB_APPLICATION_REDESIGN.md ("every transition writes an attempts row").

    Behavior:
      - status -> 'failed', failure_reason -> reason
      - if clear_materials (default True), deletes generated PDFs from
        Storage and nulls resume_pdf_path / cover_letter_pdf_path on the
        row. Disable for pre-fill failures where the tailored materials
        are still good and the user may want to retry pre-fill manually.
      - screenshot_path / uncertain_fields are persisted when present so
        the cockpit's failure banner can surface debug context (these
        flowed through the previous `mark_needs_review` alias and are
        retained on this single canonical entry point).
    """
    extras: dict = {}
    if screenshot_path:
        extras["review_screenshot"] = screenshot_path
    if uncertain_fields:
        extras["uncertain_fields"] = uncertain_fields
    if clear_materials:
        try:
            from storage import delete_all_for_job
            delete_all_for_job(job_id)
        except Exception as e:
            logger.warning(f"Could not clear materials for job {job_id}: {e}")
        extras["resume_pdf_path"] = None
        extras["cover_letter_pdf_path"] = None
    return update_job_status(job_id, "failed", failure_reason=reason, **extras)


def get_job_counts_by_status() -> dict:
    """Get a count of jobs in each status for monitoring."""
    result = client.table("jobs").select("status").execute()
    counts = {}
    for row in result.data:
        s = row.get("status", "unknown")
        counts[s] = counts.get(s, 0) + 1
    return counts
