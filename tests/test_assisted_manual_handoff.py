"""tests/test_assisted_manual_handoff.py — always-assisted-manual fallback (Part 2).

``jobpipe.submit.handoff.assisted_manual_handoff`` is the worst-case output of
the prepare flow: tab left open on the application page, tailored materials
downloaded locally ready to drag in, and a checklist of what couldn't be
filled — never a bare failure.

These tests target the helper directly with a fake page + monkeypatched
storage/db, asserting:

  - materials are downloaded to ``<handoff>/<company>_<job_id>/`` as
    ``resume.pdf`` + ``cover_letter.pdf`` + ``cover_letter.txt``;
  - a checklist (unfilled fields + role-specific questions) is built and
    written to the jobs row's ``application_notes``;
  - the row is marked ``awaiting_human_submit`` (assisted-manual hand-off,
    not a hard failure);
  - the page is NOT closed.

Offline — no Supabase, no real Playwright, no network.
"""

from __future__ import annotations

from types import SimpleNamespace

import pytest


class _FakePage:
    def __init__(self):
        self.closed = False
        self.url = "https://jobs.example.com/apply"

    def close(self):
        self.closed = True


def _job(job_id="hand-1"):
    return {
        "id": job_id,
        "company": "Acme Robotics",
        "title": "Embedded ML Engineer",
        "resume_pdf_path": f"{job_id}/resume.pdf",
        "cover_letter_pdf_path": f"{job_id}/cover_letter.pdf",
        "cover_letter_path": "Dear Acme,\n\nI build spiking networks.\n",
        "form_answers": {
            "first_name": "Vishal",
            "additional_questions": [
                {"question": "Why Acme?", "draft_answer": "Mission fit."},
                {"question": "Salary?", "draft_answer": "$120-140k"},
            ],
        },
    }


@pytest.fixture
def patched(monkeypatch, tmp_path):
    """Patch storage + db on the handoff module surface; point HANDOFF_DIR at tmp."""
    monkeypatch.setenv("JOBPIPE_HANDOFF_DIR", str(tmp_path / "handoff"))

    from jobpipe.submit import handoff as h

    downloads = []

    def _download_to_tmp(storage_path, suffix=None):
        # Materialise a fake source file the helper can copy from.
        src = tmp_path / f"src_{len(downloads)}.pdf"
        src.write_bytes(b"%PDF-" + storage_path.encode())
        downloads.append(storage_path)
        return src

    status_calls = []

    def _update_job_status(job_id, status, **extra):
        status_calls.append((job_id, status, extra))
        return {}

    monkeypatch.setattr(h, "download_to_tmp", _download_to_tmp)
    monkeypatch.setattr(h, "update_job_status", _update_job_status)
    return SimpleNamespace(
        module=h, downloads=downloads, status_calls=status_calls, tmp=tmp_path,
    )


def test_handoff_downloads_materials_writes_checklist_and_leaves_tab_open(patched):
    page = _FakePage()
    job = _job("hand-1")

    result = patched.module.assisted_manual_handoff(
        page, job,
        reason="agent gave up: unsure how to fill work-authorization dropdown",
        unfilled=["work_authorization", "years_of_experience"],
    )

    # 1. Tab stays open.
    assert page.closed is False

    # 2. Materials downloaded into <handoff>/<company>_<job_id>/.
    folder = patched.tmp / "handoff" / "Acme_Robotics_hand-1"
    assert (folder / "resume.pdf").exists()
    assert (folder / "cover_letter.pdf").exists()
    # cover_letter.txt is the plain-text body for paste fields.
    assert (folder / "cover_letter.txt").read_text() == job["cover_letter_path"]
    assert set(patched.downloads) == {"hand-1/resume.pdf", "hand-1/cover_letter.pdf"}

    # 3. Row marked awaiting_human_submit with the checklist in application_notes.
    assert len(patched.status_calls) == 1
    job_id, status, extra = patched.status_calls[0]
    assert job_id == "hand-1"
    assert status == "awaiting_human_submit"
    notes = extra["application_notes"]
    assert "work_authorization" in notes
    assert "years_of_experience" in notes
    # role-specific questions surface in the checklist too
    assert "Why Acme?" in notes or "role-specific" in notes
    # flagged as assisted-manual, not a clean pre-fill
    assert "ASSISTED-MANUAL" in notes.upper()
    assert "agent gave up" in notes
    # local materials path is in the notes so the cockpit can point at it
    assert str(folder) in notes

    # 4. Structured return for the caller.
    assert result["handoff"] is True
    assert result["materials_dir"] == str(folder)
    assert any("work_authorization" in line for line in result["checklist"])


def test_handoff_survives_a_material_download_failure(patched, monkeypatch):
    """A failed material download must NOT turn the hand-off back into a bare
    failure — note it in the checklist and still mark awaiting_human_submit."""
    def _boom(storage_path, suffix=None):
        raise RuntimeError("storage 404")

    monkeypatch.setattr(patched.module, "download_to_tmp", _boom)

    page = _FakePage()
    result = patched.module.assisted_manual_handoff(
        page, _job("hand-2"), reason="adapter exception", unfilled=[],
    )

    assert page.closed is False
    assert result["handoff"] is True
    # still marked awaiting_human_submit
    _, status, extra = patched.status_calls[0]
    assert status == "awaiting_human_submit"
    # the download failure is surfaced, not swallowed
    assert "storage 404" in extra["application_notes"] or \
        "could not download" in extra["application_notes"].lower()
