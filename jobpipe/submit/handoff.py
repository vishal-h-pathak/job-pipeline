"""handoff.py — always-assisted-manual fallback for the prepare flow.

The prepare flow's worst-case output is *not* a bare failure. Whenever it
can't finish a clean pre-fill — the agent called ``queue_for_review``, an
adapter raised, required fields were left empty, the ATS couldn't be resolved,
the page failed to load — it degrades to an "assisted-manual hand-off":

  1. the browser tab is left OPEN on the application page (this module never
     closes the page — closing on success→next is the orchestrator's job);
  2. the tailored resume + cover letter are downloaded to a predictable local
     folder the user can drag from;
  3. a checklist of what couldn't be filled is printed AND written to the jobs
     row so the cockpit can show it;
  4. the row is marked ``awaiting_human_submit`` (the same lane a clean
     pre-fill lands in — "human, take over in the open tab") with a structured
     ``ASSISTED-MANUAL`` reason that distinguishes it from a clean pre-fill.

Status note: the original spec said ``needs_review``, which is a *retired*
``jobs.status`` value (``LEGACY_STATUS_MAP`` → ``ready_for_review``) and would
raise in :func:`jobpipe.db.update_job_status`. We land the row on the canonical
``awaiting_human_submit`` instead; the ``ASSISTED-MANUAL`` marker + checklist in
``application_notes`` carry the "this was a degraded hand-off" signal.
"""

from __future__ import annotations

import logging
import os
import shutil
from pathlib import Path
from typing import Optional

from jobpipe.config import HANDOFF_DIR_DEFAULT, HANDOFF_DIR_ENV
from jobpipe.db import update_job_status
from jobpipe.shared.storage import download_to_tmp

logger = logging.getLogger("submit.handoff")

_MARKER = "ASSISTED-MANUAL HAND-OFF"


def handoff_dir() -> Path:
    """Base directory for local hand-off material folders (expanded)."""
    raw = os.environ.get(HANDOFF_DIR_ENV) or HANDOFF_DIR_DEFAULT
    return Path(raw).expanduser()


def _slug(value: str) -> str:
    return "".join(c if c.isalnum() else "_" for c in (value or "company"))[:40]


def _download_material(storage_path: str, dest: Path, label: str,
                       problems: list[str]) -> bool:
    """Copy a Storage object to ``dest``. Records a problem instead of raising
    so a single missing artifact can't turn the whole hand-off into a failure.
    """
    if not storage_path:
        problems.append(f"no {label} on the job row")
        return False
    try:
        tmp = download_to_tmp(storage_path)
    except Exception as exc:  # noqa: BLE001 — degrade, never raise
        problems.append(f"could not download {label}: {exc}")
        logger.warning("handoff: %s download failed (%s): %s", label, storage_path, exc)
        return False
    try:
        shutil.copyfile(tmp, dest)
    finally:
        try:
            Path(tmp).unlink(missing_ok=True)
        except Exception:  # noqa: BLE001
            pass
    return True


def _build_checklist(job: dict, unfilled: Optional[list],
                     problems: list[str]) -> list[str]:
    """What the human still has to do, in human-readable lines."""
    items: list[str] = []
    for f in unfilled or []:
        items.append(f"Fill: {f}")

    # Role-specific questions are policy-never-auto-filled — humans paste them
    # from the cockpit drafts. Surface each so nothing is silently dropped.
    qs = (job.get("form_answers") or {}).get("additional_questions") or []
    for q in qs:
        question = (q.get("question") or "").strip()
        if question:
            items.append(f"Answer (paste from cockpit draft): {question}")

    for p in problems:
        items.append(f"Note: {p}")

    if not items:
        items.append("Review the open tab and submit — nothing flagged unfilled.")
    return items


def assisted_manual_handoff(page, job: dict, reason: str,
                            unfilled: Optional[list] = None) -> dict:
    """Degrade a non-success prepare exit to an assisted-manual hand-off.

    Leaves ``page`` OPEN, downloads materials locally, writes a checklist to
    the jobs row, marks it ``awaiting_human_submit``, and returns a structured
    dict describing the hand-off. Best-effort throughout — a storage or DB
    hiccup is recorded in the notes rather than re-raised, so the user always
    gets "tab open + files ready + checklist".
    """
    job_id = job.get("id")
    company = job.get("company") or "Unknown"
    folder = handoff_dir() / f"{_slug(company)}_{job_id}"
    folder.mkdir(parents=True, exist_ok=True)

    problems: list[str] = []
    _download_material(job.get("resume_pdf_path"), folder / "resume.pdf",
                       "resume PDF", problems)
    _download_material(job.get("cover_letter_pdf_path"),
                       folder / "cover_letter.pdf", "cover letter PDF", problems)

    # Plain-text cover-letter body for paste fields.
    cl_text = job.get("cover_letter_path") or ""
    if cl_text:
        try:
            (folder / "cover_letter.txt").write_text(cl_text, encoding="utf-8")
        except Exception as exc:  # noqa: BLE001
            problems.append(f"could not write cover_letter.txt: {exc}")

    checklist = _build_checklist(job, unfilled, problems)
    checklist_text = "\n".join(f"  [ ] {line}" for line in checklist)

    notes = (
        f"{_MARKER}\n"
        f"Reason: {reason}\n"
        f"Materials downloaded to: {folder}\n"
        f"Application page left open for manual completion.\n"
        f"Checklist:\n{checklist_text}"
    )

    # Console hand-off banner (the user is sitting at this terminal).
    bar = "=" * 60
    print(
        f"\n{bar}\n"
        f"  ASSISTED-MANUAL HAND-OFF — {company} — {job.get('title', '')}\n"
        f"  {reason}\n"
        f"  Tab is open. Materials ready to drag in:\n"
        f"    {folder}\n"
        f"  Still to do:\n{checklist_text}\n"
        f"{bar}"
    )

    try:
        update_job_status(job_id, "awaiting_human_submit", application_notes=notes)
    except Exception as exc:  # noqa: BLE001 — never re-raise out of the fallback
        logger.error("handoff: failed to mark awaiting_human_submit for %s: %s",
                     job_id, exc)

    # Deliberately do NOT close `page` — leaving the tab open is the point.
    return {
        "handoff": True,
        "materials_dir": str(folder),
        "checklist": checklist,
        "reason": reason,
        "application_notes": notes,
        "problems": problems,
    }
