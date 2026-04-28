"""
db.py — Supabase client for job-submitter.

Narrow responsibility: read jobs that are ready for submission, write
state transitions back. Does NOT own the schema — the tailor writes the
incoming fields; we only touch submitter-specific columns.

Schema contract is documented in CLAUDE.md; migrations live in migrations/.
"""

from __future__ import annotations

import hashlib
import json
import logging
from datetime import datetime, timezone
from typing import Any

from supabase import create_client

from config import (
    SUPABASE_KEY,
    SUPABASE_SERVICE_ROLE_KEY,
    SUPABASE_URL,
)

logger = logging.getLogger("submitter.db")

# Two clients: anon for reads, service-role for Storage operations.
client = create_client(SUPABASE_URL, SUPABASE_KEY)
service_client = create_client(SUPABASE_URL, SUPABASE_SERVICE_ROLE_KEY)


# ── Reads ────────────────────────────────────────────────────────────────

def get_jobs_ready_for_submission(limit: int = 10) -> list[dict]:
    """
    Returns jobs the tailor has finished preparing, oldest-first so high-score
    jobs don't indefinitely starve lower-score ones if the former keep failing.

    Consumes both legacy 'ready_to_submit' status and the forthcoming
    'tailored' status so the submitter works across the transition.
    """
    result = (
        client.table("jobs")
        .select("*")
        .in_("status", ["ready_to_submit", "tailored"])
        .order("status_updated_at", desc=False)
        .limit(limit)
        .execute()
    )
    return result.data or []


def get_job(job_id: str) -> dict | None:
    result = client.table("jobs").select("*").eq("id", job_id).execute()
    rows = result.data or []
    return rows[0] if rows else None


def next_attempt_n(job_id: str) -> int:
    """Monotonically increasing attempt counter for this job."""
    result = (
        client.table("application_attempts")
        .select("attempt_n")
        .eq("job_id", job_id)
        .order("attempt_n", desc=True)
        .limit(1)
        .execute()
    )
    rows = result.data or []
    return (rows[0]["attempt_n"] + 1) if rows else 1


# ── Writes ───────────────────────────────────────────────────────────────

def _utcnow() -> str:
    return datetime.now(timezone.utc).isoformat()


def mark_submitting(job_id: str) -> None:
    """Claim a job at the start of a submission attempt."""
    client.table("jobs").update({
        "status": "submitting",
        "status_updated_at": _utcnow(),
    }).eq("id", job_id).execute()


def record_submission_log(job_id: str, log: dict, confidence: float | None) -> None:
    """Overwrite submission_log and confidence on the jobs row."""
    client.table("jobs").update({
        "submission_log": log,
        "confidence": confidence,
    }).eq("id", job_id).execute()


def mark_submitted(job_id: str, confirmation_evidence: dict) -> None:
    client.table("jobs").update({
        "status": "submitted",
        "status_updated_at": _utcnow(),
    }).eq("id", job_id).execute()
    logger.info("job %s submitted (%s)", job_id, confirmation_evidence.get("kind"))


def mark_needs_review(job_id: str, reason: str, packet_ref: str | None = None) -> None:
    """Submit-side `needs_review` transition for ambiguous post-submit pages.

    PR-6 made this the single canonical `mark_needs_review` — the M-2
    deprecated alias on the tailor side (which actually wrote
    status='failed') was deleted. Tailor-side failures now route to
    ``jobpipe.tailor.db.mark_tailor_failed`` instead.
    """
    extras: dict[str, Any] = {
        "status": "needs_review",
        "status_updated_at": _utcnow(),
        "failure_reason": reason,
    }
    if packet_ref:
        extras["review_packet"] = packet_ref
    client.table("jobs").update(extras).eq("id", job_id).execute()
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
    ``record_submission_log`` so a failure is observable in the cockpit
    even if the log-write call itself failed earlier in the attempt.
    """
    client.table("jobs").update({
        "status": "failed",
        "status_updated_at": _utcnow(),
        "failure_reason": reason,
    }).eq("id", job_id).execute()
    logger.info("job %s -> failed (%s)", job_id, reason)


# ── application_attempts audit rows ──────────────────────────────────────

def open_attempt(job_id: str, attempt_n: int, adapter: str) -> int:
    """Insert a new in_progress attempt row; returns its id."""
    result = client.table("application_attempts").insert({
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
    client.table("application_attempts").update({
        "ended_at": _utcnow(),
        "outcome": outcome,
        "confidence": confidence,
        "stagehand_session_id": stagehand_session_id,
        "browserbase_replay_url": browserbase_replay_url,
        "notes": notes,
    }).eq("id", attempt_id).execute()


# ── Materials integrity ──────────────────────────────────────────────────

def verify_materials_hash(job: dict, resume_bytes: bytes, cover_letter_text: str) -> bool:
    """
    Compare the job's stored materials_hash against a fresh hash of the
    materials we just downloaded. Refuses to submit on mismatch to protect
    against drift between approval and submission.
    """
    expected = job.get("materials_hash")
    if not expected:
        logger.warning("job %s has no materials_hash — proceeding without verify", job["id"])
        return True
    h = hashlib.sha256()
    h.update(resume_bytes)
    h.update(b"\x1e")  # record separator between PDF and CL
    h.update(cover_letter_text.encode("utf-8"))
    actual = h.hexdigest()
    if actual != expected:
        logger.error("materials hash mismatch for job %s (expected %s, got %s)",
                     job["id"], expected[:12], actual[:12])
        return False
    return True
