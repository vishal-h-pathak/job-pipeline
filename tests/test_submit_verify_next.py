"""Part B (#4) — verification summary + stop-and-wait advance.

Two new units:

  - ``jobpipe.submit.verify.build_prefill_verification(result, ats)`` turns an
    adapter fill result into ``{filled, required, still_needs, summary}`` — the
    "filled X of Y; still needs: ..." line the cockpit renders.
  - ``jobpipe.tailor.pipeline._wait_for_human_decision(job_id, page)`` replaces
    the old ``input()`` block: it polls the job row until the human flips it to
    a terminal decision (applied / skipped) in the dashboard, then closes the
    tab so the loop advances to the next job.
"""

from __future__ import annotations

from jobpipe.submit.verify import build_prefill_verification


# ── build_prefill_verification ─────────────────────────────────────────────

def test_verification_one_required_field_empty():
    # Greenhouse required labels: First Name, Last Name, Email, Phone, Resume.
    result = {"success": True, "fields_filled": ["First Name"], "required_empty": ["Phone"]}
    v = build_prefill_verification(result, "greenhouse")
    assert v["required"] == 5
    assert v["filled"] == 4
    assert v["still_needs"] == ["Phone"]
    assert v["summary"] == "filled 4 of 5 required field(s); still needs: Phone"


def test_verification_all_required_present():
    result = {"success": True, "fields_filled": ["First Name"], "required_empty": []}
    v = build_prefill_verification(result, "greenhouse")
    assert v["filled"] == 5
    assert v["required"] == 5
    assert v["still_needs"] == []
    assert "all required fields present" in v["summary"]


def test_verification_unknown_ats_has_no_field_map():
    """A non-deterministic ATS (Workday etc.) has no field map — the summary
    degrades gracefully and falls back to the agent's uncertain_fields."""
    result = {"success": False, "uncertain_fields": ["work_authorization"]}
    v = build_prefill_verification(result, "workday")
    assert v["required"] is None
    assert v["still_needs"] == ["work_authorization"]
    assert "work_authorization" in v["summary"]


# ── _wait_for_human_decision ───────────────────────────────────────────────

class _StubPage:
    def __init__(self, url="https://ats.example/apply"):
        self.url = url
        self.closed = False

    def close(self):
        self.closed = True


def _seq_get_job(monkeypatch, statuses):
    """Patch pipeline.get_job to walk a sequence of statuses on each poll."""
    from jobpipe.tailor import pipeline as p
    calls = {"n": 0}

    def fake_get_job(job_id):
        i = min(calls["n"], len(statuses) - 1)
        calls["n"] += 1
        return {"id": job_id, "status": statuses[i]}

    monkeypatch.setattr(p, "get_job", fake_get_job)
    return p, calls


def test_wait_returns_applied_and_closes_tab(monkeypatch):
    p, calls = _seq_get_job(monkeypatch, ["applied"])
    page = _StubPage()
    decision = p._wait_for_human_decision(page=page, job_id="j1", sleep=lambda s: None)
    assert decision == "applied"
    assert page.closed is True


def test_wait_returns_skipped_and_closes_tab(monkeypatch):
    p, calls = _seq_get_job(monkeypatch, ["skipped"])
    page = _StubPage()
    decision = p._wait_for_human_decision(page=page, job_id="j2", sleep=lambda s: None)
    assert decision == "skipped"
    assert page.closed is True


def test_wait_polls_until_terminal(monkeypatch):
    # Two awaiting polls, then applied.
    p, calls = _seq_get_job(
        monkeypatch,
        ["awaiting_human_submit", "awaiting_human_submit", "applied"],
    )
    page = _StubPage()
    slept = []
    decision = p._wait_for_human_decision(
        page=page, job_id="j3", sleep=lambda s: slept.append(s),
    )
    assert decision == "applied"
    assert page.closed is True
    # Slept between the two non-terminal polls (not after the terminal read).
    assert len(slept) == 2


def test_wait_closes_tab_on_keyboard_interrupt(monkeypatch):
    from jobpipe.tailor import pipeline as p

    def boom(job_id):
        raise KeyboardInterrupt

    monkeypatch.setattr(p, "get_job", boom)
    page = _StubPage()
    decision = p._wait_for_human_decision(page=page, job_id="j4", sleep=lambda s: None)
    # Interrupt -> no decision, but the tab is still closed so we don't leak it.
    assert decision is None
    assert page.closed is True


# ── Integration: verification written to the row + tab advances ─────────────

class _IntegPage:
    def __init__(self, url="https://boards.greenhouse.io/acme/jobs/1"):
        self.url = url
        self.closed = False

    def goto(self, url, wait_until=None, timeout=None):
        return None

    def wait_for_load_state(self, *a, **k):
        return None

    def screenshot(self, full_page=False):
        return b"\x89PNG"

    def close(self):
        self.closed = True


class _IntegContext:
    def __init__(self, page):
        self._page = page

    def new_page(self):
        return self._page


class _FillAdapter:
    """Greenhouse-shaped adapter: clean success but Phone left empty."""

    name = "greenhouse"

    def fill_form(self, page, job, resume_path=None, cover_letter_path=None):
        return {
            "success": True,
            "fields_filled": ["First Name", "Last Name", "Email", "Resume"],
            "required_empty": ["Phone"],
            "notes": "Filled 4 fields",
        }


def test_prefill_writes_verification_summary_and_advances(monkeypatch, tmp_path):
    """A clean fill that left one required field empty writes
    'filled 4 of 5 ...; still needs: Phone' to the row, uploads a screenshot,
    and the stop-and-wait advance closes the tab once the row flips to
    applied."""
    from jobpipe.tailor import pipeline as p

    recorded: dict = {}
    resume = tmp_path / "r.pdf"
    resume.write_bytes(b"%PDF fake")

    monkeypatch.setattr(p, "next_attempt_n", lambda jid: 1)
    monkeypatch.setattr(p, "open_attempt", lambda jid, n, adapter: 7)
    monkeypatch.setattr(p, "close_attempt", lambda aid, **kw: recorded.setdefault("close", kw))
    monkeypatch.setattr(p, "download_to_tmp", lambda key: str(resume))
    monkeypatch.setattr(
        p, "upload_prefill_screenshot",
        lambda jid, b: recorded.setdefault("screenshot", f"{jid}/p.png"),
    )
    monkeypatch.setattr(
        p, "mark_awaiting_submit",
        lambda jid, **kw: recorded.setdefault("awaiting", (jid, kw)),
    )
    monkeypatch.setattr(
        p, "record_prefill_verification",
        lambda jid, v: recorded.setdefault("verification", (jid, v)),
    )
    monkeypatch.setattr(p, "send_awaiting_submit", lambda *a, **k: None)
    # Advance: the human flipped the row to applied.
    monkeypatch.setattr(p, "get_job", lambda jid: {"id": jid, "status": "applied"})

    page = _IntegPage()
    job = {
        "id": "vj-1", "company": "Acme", "title": "Engineer",
        "application_url": "https://boards.greenhouse.io/acme/jobs/1",
        "resume_pdf_path": "vj-1/resume.pdf",
        "cover_letter_path": "Dear Acme,\n\nHi.\n",
        "form_answers": {"first_name": "Test"},
    }

    import json as _json
    p._prefill_one_job(
        job, _IntegContext(page),
        detect_ats=lambda url: "greenhouse",
        get_applicant=lambda url: _FillAdapter(),
        UniversalApplicant=type("U", (), {}),  # adapter is NOT a UniversalApplicant
        resolve_application_url=lambda url: {
            "resolved": url, "is_ats": True, "trail": [], "notes": "ok",
        },
        json=_json,
    )

    # Screenshot uploaded.
    assert recorded.get("screenshot") == "vj-1/p.png"

    # Human-readable summary written to the row via mark_awaiting_submit.
    jid, kw = recorded["awaiting"]
    assert jid == "vj-1"
    assert kw["application_notes"] == "filled 4 of 5 required field(s); still needs: Phone"
    assert kw["screenshot_path"] == "vj-1/p.png"

    # Structured count persisted (jobs.submission_log).
    _, v = recorded["verification"]
    assert v["filled"] == 4 and v["required"] == 5
    assert v["still_needs"] == ["Phone"]

    # Stop-and-wait advance closed the tab.
    assert page.closed is True
