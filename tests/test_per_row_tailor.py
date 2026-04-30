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
