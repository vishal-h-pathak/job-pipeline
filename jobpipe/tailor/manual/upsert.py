"""upsert_manual_job — write a manual-flow jobs row to Supabase.

Cannot reuse ``jobpipe.db.upsert_job`` — that helper hardcodes
``status='new'`` and is shaped around the hunt's score/tier/reasoning
fields. The manual flow writes ``status='approved'`` (high
confidence) or ``'discovered'`` (low confidence — Amendment 1) and
stamps ``source='manual'`` so downstream code can tell the row
apart from a discovered hunt row.

The collision guard prevents a pasted URL from clobbering a row
that's already mid-pipeline — make_job_id is deterministic on
(canonical_url, title, company), so a re-paste of an
already-discovered role would collide.
"""

from __future__ import annotations

import logging
from datetime import datetime, timezone

from jobpipe import db as _db_module
from jobpipe.shared.jobid import make_job_id

from . import ScrapedPosting

logger = logging.getLogger("tailor.manual.upsert")

# Statuses where a manual upsert may safely overwrite the existing row:
# nothing has been generated against it yet (or it was already
# user-rejected). Anything else means materials, attempts, or applied
# state we must NOT silently flip back to 'approved'.
_SAFE_TO_OVERWRITE = frozenset({"new", "discovered", "ignored", "expired"})


class CollisionError(RuntimeError):
    """A manual upsert would clobber a row that's already mid-pipeline."""

    def __init__(self, job_id: str, existing_status: str):
        super().__init__(
            f"manual upsert blocked: jobs.id={job_id} exists with "
            f"status={existing_status!r} (not in {sorted(_SAFE_TO_OVERWRITE)!r}). "
            "Act on the existing row in the dashboard first, or wait for it "
            "to transition out of its current state."
        )
        self.job_id = job_id
        self.existing_status = existing_status


def _final_status(posting: ScrapedPosting) -> str:
    """Map confidence → jobs.status. Amendment 1 semantics."""
    return "approved" if posting.confidence == "high" else "discovered"


def _row_payload(
    posting: ScrapedPosting, *, job_id: str, final_status: str
) -> dict:
    """Fields written on both insert and update paths."""
    return {
        "id": job_id,
        "title": posting.title.strip(),
        "company": posting.company,
        "location": posting.location,
        "description": posting.description,
        "url": posting.url,
        "source": "manual",
        "ats_kind": posting.ats_kind,
        "status": final_status,
        "status_updated_at": datetime.now(timezone.utc).isoformat(),
    }


def upsert_manual_job(posting: ScrapedPosting) -> tuple[str, str]:
    """Write the manual jobs row. Returns (job_id, final_status).

    final_status:
        'approved'   on confidence='high' — tailoring should proceed inline
        'discovered' on confidence='low'  — surface review URL, no tailor

    Raises:
        CollisionError: if a row with the same job_id already exists in
            a status outside _SAFE_TO_OVERWRITE.
    """
    title = posting.title.strip()
    company = (posting.company or "").strip()
    job_id = make_job_id(posting.url, title, company)
    final_status = _final_status(posting)

    client = _db_module.client

    existing = (
        client.table("jobs")
        .select("id, status")
        .eq("id", job_id)
        .execute()
        .data
        or []
    )

    if existing:
        cur_status = (existing[0].get("status") or "").strip()
        if cur_status not in _SAFE_TO_OVERWRITE:
            raise CollisionError(job_id, cur_status)
        logger.info(
            "manual: updating existing row %s (was status=%r → %r)",
            job_id, cur_status, final_status,
        )
        payload = _row_payload(
            posting, job_id=job_id, final_status=final_status
        )
        # Don't overwrite id on UPDATE — strip it.
        payload.pop("id", None)
        client.table("jobs").update(payload).eq("id", job_id).execute()
    else:
        logger.info(
            "manual: inserting new row %s with status=%r",
            job_id, final_status,
        )
        client.table("jobs").insert(
            _row_payload(posting, job_id=job_id, final_status=final_status)
        ).execute()

    return job_id, final_status
