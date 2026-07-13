"""tests/test_prefill_truth_and_timeout.py — ``_wait_for_human_decision``
browser-truth capture (P0 #1) and decision-wait timeout re-queue (hygiene #3).

Exercises ``jobpipe.tailor.pipeline._wait_for_human_decision`` directly
against a stub sync Page — no real Playwright, no Supabase, no notify
network calls (every DB / Storage / notify collaborator it touches is
monkeypatched on the ``pipeline`` module surface, same pattern as
``tests/test_submit_verify_next.py``).
"""

from __future__ import annotations


# ── Stub Page infrastructure (mirrors tests/test_page_truth.py) ───────────

class _ErrEl:
    def __init__(self, text: str = "", visible: bool = True):
        self._text = text
        self._visible = visible

    def is_visible(self, timeout: int = 500) -> bool:
        return self._visible

    def text_content(self) -> str:
        return self._text


class _ErrLocator:
    def __init__(self, els: list[_ErrEl]):
        self._els = els

    def count(self) -> int:
        return len(self._els)

    def nth(self, i: int) -> _ErrEl:
        return self._els[i]


class _WaitPage:
    def __init__(self, *, url: str = "https://ats.example/apply",
                 content_str: str = "", error_map: dict | None = None):
        self.url = url
        self.closed = False
        self._content = content_str
        self._error_map = error_map or {}

    def close(self) -> None:
        self.closed = True

    def content(self) -> str:
        return self._content

    def screenshot(self, full_page: bool = False) -> bytes:
        return b"\x89PNG"

    def locator(self, selector: str) -> _ErrLocator:
        return _ErrLocator(self._error_map.get(selector, []))


# ── Browser-truth capture (P0 #1) ───────────────────────────────────────────

def test_wait_records_truth_and_screenshot_on_terminal_decision(monkeypatch):
    from jobpipe.tailor import pipeline as p

    monkeypatch.setattr(p, "get_job", lambda jid: {"id": jid, "status": "applied"})
    recorded: dict = {}
    monkeypatch.setattr(
        p, "upload_final_screenshot", lambda jid, b: f"{jid}/final.png",
    )
    monkeypatch.setattr(
        p, "record_attempt_truth",
        lambda aid, truth: recorded.setdefault("truth", (aid, truth)),
    )
    monkeypatch.setattr(
        p, "send_truth_mismatch",
        lambda job, truth: recorded.setdefault("mismatch_called", True),
    )

    page = _WaitPage(url="https://boards.greenhouse.io/acme/applications/thank_you")
    decision = p._wait_for_human_decision(
        page=page, job_id="j1", attempt_id=99, ats="greenhouse", job={"id": "j1"},
        sleep=lambda s: None,
    )

    assert decision == "applied"
    assert page.closed is True
    aid, truth = recorded["truth"]
    assert aid == 99
    assert truth["final_url"] == page.url
    assert truth["success_signal"]["kind"] == "url_redirect"
    assert truth["screenshot"] == "j1/final.png"
    assert "mismatch_called" not in recorded  # success signal present -> no mismatch


def test_wait_fires_truth_mismatch_notification_when_applied_but_errors_present(
    monkeypatch,
):
    from jobpipe.tailor import pipeline as p

    monkeypatch.setattr(p, "get_job", lambda jid: {"id": jid, "status": "applied"})
    monkeypatch.setattr(p, "upload_final_screenshot", lambda jid, b: "j2/final.png")
    monkeypatch.setattr(p, "record_attempt_truth", lambda aid, truth: None)
    mismatch_calls: list = []
    monkeypatch.setattr(
        p, "send_truth_mismatch",
        lambda job, truth: mismatch_calls.append((job, truth)),
    )

    page = _WaitPage(
        url="https://boards.greenhouse.io/acme/jobs/1",  # no success signal
        error_map={'[role="alert"]': [_ErrEl(text="Phone is required")]},
    )
    decision = p._wait_for_human_decision(
        page=page, job_id="j2", attempt_id=100, ats="greenhouse", job={"id": "j2"},
        sleep=lambda s: None,
    )

    assert decision == "applied"
    assert len(mismatch_calls) == 1
    job_arg, truth_arg = mismatch_calls[0]
    assert job_arg == {"id": "j2"}
    assert truth_arg["error_signals"] == ["Phone is required"]
    assert truth_arg["success_signal"] is None


def test_wait_does_not_fire_mismatch_when_decision_is_skipped(monkeypatch):
    """Mismatch notification is scoped to ``applied`` — a ``skipped`` row
    with leftover page errors is expected (the human chose not to submit),
    not a mismatch."""
    from jobpipe.tailor import pipeline as p

    monkeypatch.setattr(p, "get_job", lambda jid: {"id": jid, "status": "skipped"})
    monkeypatch.setattr(p, "upload_final_screenshot", lambda jid, b: "j3/final.png")
    monkeypatch.setattr(p, "record_attempt_truth", lambda aid, truth: None)
    mismatch_calls: list = []
    monkeypatch.setattr(
        p, "send_truth_mismatch", lambda job, truth: mismatch_calls.append(1),
    )

    page = _WaitPage(error_map={'[role="alert"]': [_ErrEl(text="whatever")]})
    decision = p._wait_for_human_decision(
        page=page, job_id="j3", attempt_id=101, ats="greenhouse", job={"id": "j3"},
        sleep=lambda s: None,
    )

    assert decision == "skipped"
    assert mismatch_calls == []


def test_wait_skips_truth_capture_entirely_when_no_attempt_id(monkeypatch):
    """``attempt_id=None`` (legacy callers) skips truth capture entirely —
    no DB write, no screenshot upload. Keeps the pre-truth-capture unit
    tests (``tests/test_submit_verify_next.py``) valid without changes."""
    from jobpipe.tailor import pipeline as p

    monkeypatch.setattr(p, "get_job", lambda jid: {"id": jid, "status": "applied"})
    calls: list = []
    monkeypatch.setattr(p, "record_attempt_truth", lambda *a, **k: calls.append("truth"))
    monkeypatch.setattr(
        p, "upload_final_screenshot", lambda *a, **k: calls.append("screenshot"),
    )

    page = _WaitPage()
    decision = p._wait_for_human_decision(page=page, job_id="j4", sleep=lambda s: None)

    assert decision == "applied"
    assert calls == []


def test_wait_truth_capture_failure_is_swallowed(monkeypatch):
    """A DB/Storage hiccup during truth capture must not prevent the tab
    from closing or the decision from being returned."""
    from jobpipe.tailor import pipeline as p

    monkeypatch.setattr(p, "get_job", lambda jid: {"id": jid, "status": "applied"})

    def _boom(*a, **k):
        raise RuntimeError("storage is down")

    monkeypatch.setattr(p, "upload_final_screenshot", _boom)
    monkeypatch.setattr(p, "record_attempt_truth", _boom)

    page = _WaitPage()
    decision = p._wait_for_human_decision(
        page=page, job_id="j5", attempt_id=102, ats="greenhouse", job={"id": "j5"},
        sleep=lambda s: None,
    )

    assert decision == "applied"
    assert page.closed is True


# ── Decision-wait timeout re-queue (hygiene #3) ─────────────────────────────

def test_wait_times_out_and_requeues(monkeypatch):
    from jobpipe.tailor import pipeline as p

    # Never reaches a terminal status.
    monkeypatch.setattr(
        p, "get_job", lambda jid: {"id": jid, "status": "awaiting_human_submit"},
    )
    monkeypatch.setattr(p, "record_attempt_truth", lambda *a, **k: None)
    monkeypatch.setattr(p, "upload_final_screenshot", lambda *a, **k: "j6/final.png")
    requeued: list = []
    monkeypatch.setattr(p, "mark_prefilling", lambda jid: requeued.append(jid))
    notified: list = []
    monkeypatch.setattr(p, "send_decision_timeout", lambda job: notified.append(job))

    page = _WaitPage()
    # Fake monotonic clock: the start read is 0.0, the first timeout check
    # already reads past the 1-minute threshold.
    clock = iter([0.0, 1000.0, 1000.0, 1000.0])
    decision = p._wait_for_human_decision(
        page=page, job_id="j6", attempt_id=102, ats="greenhouse", job={"id": "j6"},
        sleep=lambda s: None, timeout_minutes=1, now=lambda: next(clock),
    )

    assert decision == "timeout"
    assert page.closed is True
    assert requeued == ["j6"]
    assert notified and notified[0]["id"] == "j6"


def test_wait_does_not_timeout_before_threshold(monkeypatch):
    from jobpipe.tailor import pipeline as p

    monkeypatch.setattr(p, "get_job", lambda jid: {"id": jid, "status": "applied"})
    monkeypatch.setattr(p, "record_attempt_truth", lambda *a, **k: None)
    monkeypatch.setattr(p, "upload_final_screenshot", lambda *a, **k: "j7/final.png")
    requeued: list = []
    monkeypatch.setattr(p, "mark_prefilling", lambda jid: requeued.append(jid))

    page = _WaitPage()
    decision = p._wait_for_human_decision(
        page=page, job_id="j7", attempt_id=103, ats="greenhouse", job={"id": "j7"},
        sleep=lambda s: None, timeout_minutes=45, now=lambda: 0.0,
    )

    assert decision == "applied"
    assert requeued == []


def test_wait_timeout_requeue_failure_is_swallowed(monkeypatch):
    """A ``mark_prefilling`` hiccup during the timeout re-queue must not
    prevent the function from returning ``"timeout"`` and closing the tab —
    the row staying stuck in ``prefilling`` limbo is a lesser evil than the
    watcher's consumer loop crashing."""
    from jobpipe.tailor import pipeline as p

    monkeypatch.setattr(
        p, "get_job", lambda jid: {"id": jid, "status": "awaiting_human_submit"},
    )
    monkeypatch.setattr(p, "record_attempt_truth", lambda *a, **k: None)
    monkeypatch.setattr(p, "upload_final_screenshot", lambda *a, **k: "x")
    monkeypatch.setattr(p, "send_decision_timeout", lambda job: None)

    def _boom(jid):
        raise RuntimeError("db is down")

    monkeypatch.setattr(p, "mark_prefilling", _boom)

    page = _WaitPage()
    clock = iter([0.0, 1000.0, 1000.0])
    decision = p._wait_for_human_decision(
        page=page, job_id="j8", attempt_id=104, ats="greenhouse", job={"id": "j8"},
        sleep=lambda s: None, timeout_minutes=1, now=lambda: next(clock),
    )

    assert decision == "timeout"
    assert page.closed is True
