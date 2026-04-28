"""jobpipe.db — canonical Supabase data layer for the whole pipeline.

PR-8 consolidates the three per-subtree ``db.py`` files
(``jobpipe/{hunt,tailor,submit}/db.py``) into this single canonical
module. Per-subtree ``db.py`` files become thin shims that re-export
from here so the unprefixed-import pattern PR-3/4/5 set up keeps working.

Tables touched:
    - ``jobs``                  — main pipeline row (hunt writes; tailor /
                                   submit transition status)
    - ``application_attempts``  — audit trail per submit attempt
    - ``notifications``         — written by ``jobpipe.notify`` (not here)
    - ``star_stories``          — reserved (no helpers here yet; PR-9 will
                                   move tailor's star-story queries in
                                   when those modules consolidate)

Behavior contract (preserves PR-6's failure split):
    - ``mark_tailor_failed`` is the canonical tailor-side failure
      transition (rename from the M-2 ``mark_failed`` did during PR-6).
    - ``mark_failed`` is the canonical submit-side failure transition.
      Required-attempt-row preconditions documented at the call site.
    - ``mark_needs_review`` is submit-only (the M-2 deprecated tailor
      alias was deleted in PR-6).

Behavior delta vs. the three pre-PR-8 sources:
    - Supabase clients: hunt used a per-call ``_client()`` factory,
      tailor/submit used eager module-level singletons. PR-8 unifies on
      a **lazy module-level singleton** matching the pattern
      ``jobpipe/tailor/notify.py`` already used. ``client`` and
      ``service_client`` remain importable as module attributes via
      module-level ``__getattr__``; first access triggers Supabase
      client creation. Why this won: fewest handshakes per job under
      polling load, and import-time has no side effects so the module
      is testable without secrets.
    - ``mark_ready_to_submit`` deprecated alias: kept (still emits
      logger.warning) — PR-8 doesn't sweep deprecation aliases.
"""

from __future__ import annotations

import hashlib
import logging
from datetime import datetime, timezone
from typing import Any

from jobpipe.config import (
    SUPABASE_KEY,
    SUPABASE_SERVICE_ROLE_KEY,
    SUPABASE_URL,
)

logger = logging.getLogger("jobpipe.db")


# ── Lazy Supabase client singletons ───────────────────────────────────────
# Two clients: anon for reads / standard writes, service-role for Storage
# operations. Both are created on first access so this module is
# importable without env vars (tests, CI).

_client = None
_service_client = None


def _get_client():
    """Return the lazily-initialised anon Supabase client."""
    global _client
    if _client is None:
        # Lazy import: keeps ``import jobpipe.db`` cheap and lets the
        # supabase SDK stay an optional dependency for tooling that
        # only needs the function signatures.
        from supabase import create_client
        _client = create_client(SUPABASE_URL, SUPABASE_KEY)
    return _client


def _get_service_client():
    """Return the lazily-initialised service-role Supabase client."""
    global _service_client
    if _service_client is None:
        from supabase import create_client
        _service_client = create_client(SUPABASE_URL, SUPABASE_SERVICE_ROLE_KEY)
    return _service_client


def __getattr__(name: str):
    """Module-level attribute access for ``client`` / ``service_client``.

    Lets existing call sites (``from db import client``,
    ``db.client.table(...)``) keep working without forcing eager init.
    Any test or shim that does ``module.client = fake`` overrides
    transparently — the assignment lands in the module dict and
    ``__getattr__`` is no longer consulted for that name.
    """
    if name == "client":
        return _get_client()
    if name == "service_client":
        return _get_service_client()
    raise AttributeError(f"module 'jobpipe.db' has no attribute {name!r}")


# ── Common helpers ────────────────────────────────────────────────────────

def _utcnow() -> str:
    """ISO-8601 UTC timestamp for status_updated_at / created_at / etc."""
    return datetime.now(timezone.utc).isoformat()


# ══════════════════════════════════════════════════════════════════════════
#  HUNT — discovery / scoring writes (was jobpipe/hunt/db.py)
# ══════════════════════════════════════════════════════════════════════════

def upsert_job(job: dict, result: dict) -> None:
    """Insert a freshly-discovered job or update score / tier / reasoning.

    Hunt's score+legitimacy fields land on the row; status defaults to
    ``"new"`` on insert.  ``created_at`` is stamped explicitly so rows
    are well-formed even if a DB default is missing.
    """
    client = _get_client()
    existing = (
        client.table("jobs").select("id").eq("id", job["id"]).execute().data or []
    )
    if existing:
        client.table("jobs").update(
            {
                "score": result.get("score"),
                "tier": result.get("tier"),
                "reasoning": result.get("reasoning"),
                "action": result.get("recommended_action"),
                "legitimacy": result.get("legitimacy"),
                "legitimacy_reasoning": result.get("legitimacy_reasoning"),
            }
        ).eq("id", job["id"]).execute()
    else:
        client.table("jobs").upsert(
            {
                "id": job["id"],
                "title": job.get("title"),
                "company": job.get("company"),
                "location": job.get("location"),
                "description": job.get("description"),
                "url": job.get("url"),
                "source": job.get("source"),
                "score": result.get("score"),
                "tier": result.get("tier"),
                "reasoning": result.get("reasoning"),
                "action": result.get("recommended_action"),
                "legitimacy": result.get("legitimacy"),
                "legitimacy_reasoning": result.get("legitimacy_reasoning"),
                "status": "new",
                "created_at": _utcnow(),
            },
            on_conflict="id",
        ).execute()


def get_seen_ids() -> set[str]:
    """All canonical job ids the hunter has already seen — for cross-source dedup."""
    rows = _get_client().table("jobs").select("id").execute().data or []
    return {r["id"] for r in rows}


# ══════════════════════════════════════════════════════════════════════════
#  TAILOR — status lifecycle for the M-2/M-6 application flow
#  (was jobpipe/tailor/db.py)
# ══════════════════════════════════════════════════════════════════════════
#
# Status lifecycle (M-2, career-ops alignment):
#     discovered (alias: new) — discovered by job-hunter
#     ignored                 — user dismissed in dashboard
#     approved                — user approved for application
#     preparing               — tailoring resume + cover letter + form_answers
#     ready_for_review        — materials ready; awaiting "Pre-fill Form" click
#     prefilling              — per-ATS DOM handler (or vision agent) running
#     awaiting_human_submit   — form pre-filled in visible browser; user
#                               must review and click Submit themselves,
#                               then click "Mark Applied" in the dashboard
#     applied                 — HUMAN clicked Mark Applied (source of truth)
#     failed                  — pre-fill or submission error
#     skipped                 — user opted out of this row
#     expired                 — posting taken down (J-8)
#
# Legacy statuses (ready_to_submit / submit_confirmed / submitting) were
# collapsed into ready_for_review by migration 007. The CHECK constraint
# on jobs.status enforces the new enum.
#
# The system NEVER auto-sets status='applied'. Only the dashboard cockpit's
# "Mark Applied" PATCH does — that click is the single source of truth for
# whether a job actually got submitted.

def get_jobs_by_status(status: str, limit: int = 10) -> list[dict]:
    """Fetch jobs with the given status, ordered by score descending."""
    result = (
        _get_client().table("jobs")
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
    """DEPRECATED — ``submit_confirmed`` no longer exists under the M-2
    CHECK enum. Returns []. Kept so legacy callers
    (``process_confirmed_jobs``, removed in M-7) don't crash on import.
    Use :func:`get_prefill_requested_jobs` instead.
    """
    logger.warning(
        "get_confirmed_jobs() is deprecated; status 'submit_confirmed' "
        "was removed in M-2. Use get_prefill_requested_jobs(). Returning []."
    )
    return []


def update_job_status(job_id: str, status: str, **extra_fields) -> dict:
    """Update a job's status and any additional fields.

    Args:
        job_id: The job's primary key.
        status: New status value (must satisfy the M-2 CHECK enum).
        **extra_fields: Additional columns to update (e.g., resume_path,
            failure_reason, screenshot path).

    Returns:
        The updated row data.
    """
    data = {
        "status": status,
        "status_updated_at": _utcnow(),
        **extra_fields,
    }
    result = _get_client().table("jobs").update(data).eq("id", job_id).execute()
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
        resume_pdf_path: Supabase Storage object key for the rendered
            resume PDF.
        cover_letter_pdf_path: Supabase Storage object key for cover
            letter PDF.
        archetype: Chosen archetype key (J-4) — persists for
            /dashboard/insights.
        archetype_confidence: Classifier confidence 0.0-1.0.
        submission_url: Real ATS apply URL post-resolution (M-3 column).
            Defaults to application_url if not supplied so callers that
            haven't been updated still get a value.
    """
    extras: dict[str, Any] = {}
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
    """DEPRECATED alias for :func:`mark_ready_for_review` (M-2). Forwards
    all arguments unchanged. Logged once per call so the sweep to update
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
            ``prefill_screenshot_path`` (M-3 column).
    """
    extras: dict[str, Any] = {
        "prefill_completed_at": _utcnow(),
    }
    if screenshot_path:
        extras["prefill_screenshot_path"] = screenshot_path
    return update_job_status(job_id, "awaiting_human_submit", **extras)


def mark_skipped(job_id: str, reason: str = None) -> dict:
    """User opted out of submitting this row from the cockpit."""
    extras: dict[str, Any] = {}
    if reason:
        extras["application_notes"] = reason
    return update_job_status(job_id, "skipped", **extras)


def mark_applied(job_id: str, application_notes: str = None,
                 submission_notes: str = None,
                 clear_materials: bool = True) -> dict:
    """Mark a job as applied — ALWAYS the result of a human click on the
    cockpit's "Mark Applied" button. Stamps both ``applied_at`` (legacy)
    and ``submitted_at`` (M-3) so analytics can rely on either.

    Args:
        application_notes: Free-text notes for the legacy column.
            Existing callers (process_confirmed_jobs, submit_one_visible
            — both removed in M-7) still write here.
        submission_notes: Free-text notes the human added in the cockpit
            modal. Persisted to the M-3 ``submission_notes`` column.
            Read by analytics + insights.
        clear_materials: When True (default), also deletes the generated
            PDFs from Supabase Storage and nulls the storage-path
            columns on the row.
    """
    now = _utcnow()
    extras: dict[str, Any] = {"applied_at": now, "submitted_at": now}
    if application_notes:
        extras["application_notes"] = application_notes
    if submission_notes:
        extras["submission_notes"] = submission_notes
    if clear_materials:
        # Deferred import so this module stays importable when storage
        # can't initialize (e.g. missing service role key during tests).
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
    _get_client().table("jobs").update({
        "resume_pdf_path": None,
        "cover_letter_pdf_path": None,
    }).eq("id", job_id).execute()


def mark_tailor_failed(job_id: str, reason: str, *,
                       clear_materials: bool = True,
                       screenshot_path: str = None,
                       uncertain_fields: list = None) -> dict:
    """Tailor-side failure transition (PR-6 split from ``mark_failed``).

    Use for failures that originate in the tailor pipeline: LaTeX compile
    error, prompt failure, missing inputs at tailoring time. Submitter
    failures use :func:`mark_failed` instead — that path requires an
    ``application_attempts`` row first per the design rule in
    JOB_APPLICATION_REDESIGN.md ("every transition writes an attempts row").

    Behavior:
      - status -> 'failed', failure_reason -> reason
      - if clear_materials (default True), deletes generated PDFs from
        Storage and nulls resume_pdf_path / cover_letter_pdf_path on the
        row. Disable for pre-fill failures where the tailored materials
        are still good and the user may want to retry pre-fill manually.
      - screenshot_path / uncertain_fields are persisted when present so
        the cockpit's failure banner can surface debug context (these
        flowed through the previous ``mark_needs_review`` alias and are
        retained on this single canonical entry point).
    """
    extras: dict[str, Any] = {}
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
    result = _get_client().table("jobs").select("status").execute()
    counts: dict[str, int] = {}
    for row in result.data:
        s = row.get("status", "unknown")
        counts[s] = counts.get(s, 0) + 1
    return counts


# ══════════════════════════════════════════════════════════════════════════
#  SUBMIT — submit-side state transitions + attempts audit
#  (was jobpipe/submit/db.py)
# ══════════════════════════════════════════════════════════════════════════

def get_jobs_ready_for_submission(limit: int = 10) -> list[dict]:
    """Jobs the tailor has finished preparing.

    Returned oldest-first so high-score jobs don't indefinitely starve
    lower-score ones if the former keep failing. Consumes both legacy
    ``ready_to_submit`` status and the forthcoming ``tailored`` status
    so the submitter works across the transition.
    """
    result = (
        _get_client().table("jobs")
        .select("*")
        .in_("status", ["ready_to_submit", "tailored"])
        .order("status_updated_at", desc=False)
        .limit(limit)
        .execute()
    )
    return result.data or []


def get_job(job_id: str) -> dict | None:
    result = _get_client().table("jobs").select("*").eq("id", job_id).execute()
    rows = result.data or []
    return rows[0] if rows else None


def next_attempt_n(job_id: str) -> int:
    """Monotonically increasing attempt counter for this job."""
    result = (
        _get_client().table("application_attempts")
        .select("attempt_n")
        .eq("job_id", job_id)
        .order("attempt_n", desc=True)
        .limit(1)
        .execute()
    )
    rows = result.data or []
    return (rows[0]["attempt_n"] + 1) if rows else 1


def mark_submitting(job_id: str) -> None:
    """Claim a job at the start of a submission attempt."""
    _get_client().table("jobs").update({
        "status": "submitting",
        "status_updated_at": _utcnow(),
    }).eq("id", job_id).execute()


def record_submission_log(job_id: str, log: dict, confidence: float | None) -> None:
    """Overwrite submission_log and confidence on the jobs row."""
    _get_client().table("jobs").update({
        "submission_log": log,
        "confidence": confidence,
    }).eq("id", job_id).execute()


def mark_submitted(job_id: str, confirmation_evidence: dict) -> None:
    _get_client().table("jobs").update({
        "status": "submitted",
        "status_updated_at": _utcnow(),
    }).eq("id", job_id).execute()
    logger.info("job %s submitted (%s)", job_id, confirmation_evidence.get("kind"))


def mark_needs_review(job_id: str, reason: str, packet_ref: str | None = None) -> None:
    """Submit-side ``needs_review`` transition for ambiguous post-submit pages.

    PR-6 made this the single canonical ``mark_needs_review`` — the M-2
    deprecated alias on the tailor side (which actually wrote
    status='failed') was deleted. Tailor-side failures now route to
    :func:`mark_tailor_failed` instead.
    """
    extras: dict[str, Any] = {
        "status": "needs_review",
        "status_updated_at": _utcnow(),
        "failure_reason": reason,
    }
    if packet_ref:
        extras["review_packet"] = packet_ref
    _get_client().table("jobs").update(extras).eq("id", job_id).execute()
    logger.info("job %s -> needs_review (%s)", job_id, reason)


def mark_failed(job_id: str, reason: str) -> None:
    """Submit-side failure transition (PR-6: split from the tailor's version).

    Contract per JOB_APPLICATION_REDESIGN.md ("every state transition
    writes a row to application_attempts"): on the runner critical path,
    callers must have already opened (and will subsequently close) an
    ``application_attempts`` row. The two pre-attempt-row callsites in
    ``runner.py`` (max-attempts ceiling and materials hydration) are
    intentional exceptions documented at the call sites — they fail the
    job before any browser session is opened.

    The structured ``submission_log`` is written separately by
    :func:`record_submission_log` so a failure is observable in the
    cockpit even if the log-write call itself failed earlier in the
    attempt.
    """
    _get_client().table("jobs").update({
        "status": "failed",
        "status_updated_at": _utcnow(),
        "failure_reason": reason,
    }).eq("id", job_id).execute()
    logger.info("job %s -> failed (%s)", job_id, reason)


# ── application_attempts audit rows ──────────────────────────────────────

def open_attempt(job_id: str, attempt_n: int, adapter: str) -> int:
    """Insert a new in_progress attempt row; returns its id."""
    result = _get_client().table("application_attempts").insert({
        "job_id": job_id,
        "attempt_n": attempt_n,
        "started_at": _utcnow(),
        "outcome": "in_progress",
        "adapter": adapter,
    }).execute()
    return result.data[0]["id"]


def close_attempt(
    attempt_id: int,
    outcome: str,
    confidence: float | None = None,
    stagehand_session_id: str | None = None,
    browserbase_replay_url: str | None = None,
    notes: dict | None = None,
) -> None:
    _get_client().table("application_attempts").update({
        "ended_at": _utcnow(),
        "outcome": outcome,
        "confidence": confidence,
        "stagehand_session_id": stagehand_session_id,
        "browserbase_replay_url": browserbase_replay_url,
        "notes": notes,
    }).eq("id", attempt_id).execute()


# ── Materials integrity ──────────────────────────────────────────────────

def verify_materials_hash(job: dict, resume_bytes: bytes,
                          cover_letter_text: str) -> bool:
    """Compare ``job.materials_hash`` against a fresh hash of the
    materials we just downloaded.

    Refuses to submit on mismatch to protect against drift between
    approval and submission.
    """
    expected = job.get("materials_hash")
    if not expected:
        logger.warning(
            "job %s has no materials_hash — proceeding without verify",
            job["id"],
        )
        return True
    h = hashlib.sha256()
    h.update(resume_bytes)
    h.update(b"\x1e")  # record separator between PDF and CL
    h.update(cover_letter_text.encode("utf-8"))
    actual = h.hexdigest()
    if actual != expected:
        logger.error(
            "materials hash mismatch for job %s (expected %s, got %s)",
            job["id"], expected[:12], actual[:12],
        )
        return False
    return True
