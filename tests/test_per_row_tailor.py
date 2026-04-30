"""tests/test_per_row_tailor.py — PR-14 per-row tailor wiring.

Pins the ``--job-id`` flag's dispatch contract on ``run_tailor_only``:

  * Without ``--job-id``, ``run_tailor_only()`` calls ``process_approved_jobs``
    (the bulk path — what cron / no-arg dashboard click triggers).
  * With ``--job-id <uuid>``, ``run_tailor_only()`` calls
    ``process_one_approved_job(job_id)`` instead and skips
    ``process_approved_jobs`` entirely.

Stays at the dispatch boundary on purpose — does NOT exercise the
tailor pipeline itself (resume / cover-letter / LaTeX / form-answers
are covered by their own tests). Stubs ``process_approved_jobs`` and
``process_one_approved_job`` to recording callables so a regression in
the argparse / branching logic is caught without dragging Anthropic,
Supabase, or pdflatex into the test.
"""

from __future__ import annotations

import pytest

from jobpipe.tailor import pipeline


def _stub_recorder(name: str, calls: list):
    def _recorded(*args, **kwargs):
        calls.append((name, args, kwargs))
        return None
    return _recorded


def test_run_tailor_only_no_job_id_calls_process_approved_jobs(
    monkeypatch: pytest.MonkeyPatch,
):
    """Bulk path: bare ``jobpipe-tailor --once`` calls process_approved_jobs."""
    calls: list = []
    monkeypatch.setattr(
        pipeline,
        "process_approved_jobs",
        _stub_recorder("process_approved_jobs", calls),
    )
    monkeypatch.setattr(
        pipeline,
        "process_one_approved_job",
        _stub_recorder("process_one_approved_job", calls),
    )
    monkeypatch.setattr("sys.argv", ["jobpipe-tailor", "--once"])

    pipeline.run_tailor_only()

    names = [c[0] for c in calls]
    assert names == ["process_approved_jobs"], (
        "Expected process_approved_jobs to be called once, got: " + repr(calls)
    )


def test_run_tailor_only_with_job_id_calls_process_one_approved_job(
    monkeypatch: pytest.MonkeyPatch,
):
    """Per-row path: ``--job-id <uuid>`` calls process_one_approved_job(uuid)."""
    calls: list = []
    monkeypatch.setattr(
        pipeline,
        "process_approved_jobs",
        _stub_recorder("process_approved_jobs", calls),
    )
    monkeypatch.setattr(
        pipeline,
        "process_one_approved_job",
        _stub_recorder("process_one_approved_job", calls),
    )
    target = "b01dc6a188ecb533"
    monkeypatch.setattr(
        "sys.argv", ["jobpipe-tailor", "--once", "--job-id", target]
    )

    pipeline.run_tailor_only()

    assert len(calls) == 1, "Expected exactly one dispatch call"
    name, args, kwargs = calls[0]
    assert name == "process_one_approved_job"
    assert args == (target,), (
        f"Expected process_one_approved_job({target!r}), got args={args!r}"
    )
    # process_approved_jobs MUST NOT have been called when --job-id is present.
    assert "process_approved_jobs" not in [c[0] for c in calls]


def test_process_one_approved_job_skips_when_row_missing(
    monkeypatch: pytest.MonkeyPatch,
):
    """Stale dashboard click on a deleted row: no-op + log, no transitions."""
    monkeypatch.setattr(pipeline, "get_job", lambda _id: None)

    transitions: list = []
    monkeypatch.setattr(
        pipeline, "mark_preparing",
        lambda *a, **kw: transitions.append(("mark_preparing", a, kw)),
    )
    monkeypatch.setattr(
        pipeline, "mark_tailor_failed",
        lambda *a, **kw: transitions.append(("mark_tailor_failed", a, kw)),
    )

    pipeline.process_one_approved_job("nonexistent-id")

    assert transitions == [], (
        "Expected no status transitions when the row is missing; got "
        + repr(transitions)
    )


def test_process_one_approved_job_skips_when_status_not_approved(
    monkeypatch: pytest.MonkeyPatch,
):
    """Stale dashboard click on a row another process moved on from."""
    monkeypatch.setattr(
        pipeline,
        "get_job",
        lambda _id: {"id": _id, "status": "ready_for_review"},
    )

    transitions: list = []
    monkeypatch.setattr(
        pipeline, "mark_preparing",
        lambda *a, **kw: transitions.append(("mark_preparing", a, kw)),
    )
    monkeypatch.setattr(
        pipeline, "mark_tailor_failed",
        lambda *a, **kw: transitions.append(("mark_tailor_failed", a, kw)),
    )

    pipeline.process_one_approved_job("b01dc6a188ecb533")

    assert transitions == [], (
        "Expected no status transitions when status is not 'approved'; got "
        + repr(transitions)
    )


# ── Score-gate hotfix tests ──────────────────────────────────────────────────
# The score >= 6 gate moved from process_one_approved_job to
# process_approved_jobs so per-row dashboard clicks always run the full
# pipeline (including form_answers generation, which prefill depends on)
# while the bulk-cron path keeps its cost guard.


def _make_full_pipeline_stubs(
    monkeypatch: pytest.MonkeyPatch, score: int,
):
    """Stub every external dep ``process_one_approved_job`` touches.

    Returns ``(captured, fake_job)``. ``captured`` records
    form_answers calls and DB updates for assertion. The stubs let
    the orchestration body of process_one_approved_job execute
    without LLM calls, Supabase round-trips, LaTeX compilation, or
    HTTP fetches.
    """
    captured: dict = {"form_answers_calls": [], "db_updates": []}

    fake_job = {
        "id": "abc1234567890abc",
        "status": "approved",
        "company": "Test Co",
        "title": "Test Role",
        "url": "https://example.com/jobs/1",
        "score": score,
    }

    def _form_answers_stub(job, resume_result, archetype_meta=None):
        captured["form_answers_calls"].append(job["id"])
        return {"why_this_role": "stub", "additional_questions": []}

    class _DbChain:
        def __init__(self):
            self.payload = None

        def update(self, payload):
            self.payload = payload
            return self

        def eq(self, _key, value):
            captured["db_updates"].append(
                {"payload": self.payload, "id": value}
            )
            return self

        def execute(self):
            return type("R", (), {"data": [{"id": "ok"}]})()

    class _DbClient:
        def table(self, _name):
            return _DbChain()

    fake_db = _DbClient()

    monkeypatch.setattr(pipeline, "get_job", lambda _id: fake_job)
    monkeypatch.setattr(pipeline, "detect_ats", lambda _u: "greenhouse")
    monkeypatch.setattr(pipeline, "mark_preparing", lambda *a, **kw: None)
    monkeypatch.setattr(
        pipeline, "tailor_resume",
        lambda j: {
            "tailored_summary": "x",
            "_archetype": {"archetype": "tier_1a", "confidence": 0.9},
        },
    )
    monkeypatch.setattr(
        pipeline, "generate_cover_letter",
        lambda j, r: {"cover_letter": "Dear team..."},
    )
    monkeypatch.setattr(
        pipeline, "generate_tailored_latex",
        lambda j, r: {"compile_success": True, "pdf_bytes": b"%PDF fake"},
    )
    monkeypatch.setattr(
        pipeline, "render_cover_letter_pdf",
        lambda *a, **kw: b"%PDF cover",
    )
    monkeypatch.setattr(
        pipeline, "upload_pdf",
        lambda jid, kind, body: f"job-materials/{jid}/{kind}.pdf",
    )
    monkeypatch.setattr(pipeline, "get_applicant", lambda _u: None)
    monkeypatch.setattr(pipeline, "generate_stories", lambda *a, **kw: [])
    monkeypatch.setattr(pipeline, "save_stories", lambda *a, **kw: None)
    monkeypatch.setattr(pipeline, "mark_ready_for_review", lambda *a, **kw: None)
    monkeypatch.setattr(pipeline, "send_awaiting_review", lambda *a, **kw: None)
    monkeypatch.setattr(pipeline, "generate_form_answers", _form_answers_stub)

    # Lazy imports inside the function — patch source modules so the
    # `from X import Y` statements pick up the stubs.
    import url_resolver
    monkeypatch.setattr(
        url_resolver, "resolve_application_url",
        lambda u: {"resolved": u, "notes": "stub"},
    )

    import jobpipe.db as _dbmod
    monkeypatch.setattr(_dbmod, "_client", fake_db)
    # Belt-and-braces: tests/test_mark_failed_split.py:175 assigns
    # ``db.client = fake`` directly (NOT via monkeypatch), which
    # persists as a real module attribute and bypasses
    # jobpipe.db.__getattr__. Patching the attribute directly here
    # ensures ``from jobpipe.db import client`` inside the function
    # body picks up our stub regardless of prior-test pollution.
    monkeypatch.setattr(_dbmod, "client", fake_db, raising=False)

    return captured, fake_job


def test_per_row_tailor_generates_form_answers_below_threshold(
    monkeypatch: pytest.MonkeyPatch,
):
    """Per-row click on a score=4 row still generates form_answers.

    The hotfix's whole point: clicking the dashboard "Tailor" button
    on a low-score card must run the full pipeline (otherwise prefill
    later fails because the identity-field map is blank).
    """
    captured, fake_job = _make_full_pipeline_stubs(monkeypatch, score=4)

    pipeline.process_one_approved_job(fake_job["id"])

    assert captured["form_answers_calls"] == [fake_job["id"]], (
        "Expected generate_form_answers called exactly once for "
        f"{fake_job['id']}, got {captured['form_answers_calls']}"
    )

    assert len(captured["db_updates"]) == 1, (
        "Expected exactly one DB update (the form_answers persist), "
        f"got {captured['db_updates']}"
    )
    update = captured["db_updates"][0]
    assert "form_answers" in update["payload"], (
        f"DB update payload missing form_answers key: {update['payload']}"
    )
    assert update["payload"]["form_answers"]["why_this_role"] == "stub"
    assert update["id"] == fake_job["id"]


def test_bulk_tailor_skips_low_score(monkeypatch: pytest.MonkeyPatch):
    """Bulk path keeps the cost guard.

    process_approved_jobs delegates to process_one_approved_job only
    for rows whose score is at or above SCORE_THRESHOLD. Score=4 row
    is logged-and-skipped; score=8 row gets the full pipeline.
    """
    fake_jobs = [
        {"id": "high0000000000hi", "score": 8, "company": "HighCo"},
        {"id": "low00000000000lo", "score": 4, "company": "LowCo"},
    ]
    monkeypatch.setattr(pipeline, "get_approved_jobs", lambda: fake_jobs)

    delegated: list = []
    monkeypatch.setattr(
        pipeline, "process_one_approved_job",
        lambda jid: delegated.append(jid),
    )

    pipeline.process_approved_jobs()

    assert delegated == ["high0000000000hi"], (
        "Expected only the score=8 row to be delegated to "
        f"process_one_approved_job; got {delegated}"
    )
