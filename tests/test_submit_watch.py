"""tests/test_submit_watch.py — local submit watcher (feat/submit-watch).

All mocked: no real browser, Supabase, or network. Exercises the
:class:`jobpipe.submit.watch.SubmitWatcher` orchestration through injected
collaborators, the poll fallback source, the Realtime payload id-extraction, and
the ``channel="chrome"`` browser wiring.
"""

from __future__ import annotations

import threading

import pytest

from jobpipe.submit.watch import (
    SubmitWatcher,
    PollEventSource,
    RealtimeEventSource,
    RealtimeSubscriptionGuard,
    extract_job_id,
    _CATCHUP,
)


# ── helpers ──────────────────────────────────────────────────────────────────
class _Recorder:
    """A fake process_one that records call order and asserts no overlap."""

    def __init__(self):
        self.calls: list[str] = []
        self.max_concurrency = 0
        self._live = 0

    def __call__(self, job, context):
        self._live += 1
        self.max_concurrency = max(self.max_concurrency, self._live)
        self.calls.append(job["id"])
        self._live -= 1


def _job(job_id, status="prefilling"):
    return {"id": job_id, "status": status, "company": "Acme"}


def _make_watcher(*, pending=None, rows=None, process=None, make_source=None):
    """Build a SubmitWatcher over fakes. ``rows`` maps id -> row for fetch_one;
    defaults to a prefilling row for any requested id."""
    pending = pending if pending is not None else []
    fetch_pending_calls = {"n": 0}

    def fetch_pending():
        fetch_pending_calls["n"] += 1
        # Return the *current* backlog each call; tests mutate `pending`.
        return list(pending)

    def fetch_one(job_id):
        if rows is not None:
            return rows.get(job_id)
        return _job(job_id)

    watcher = SubmitWatcher(
        open_context=lambda: (object(), lambda: None),
        fetch_pending=fetch_pending,
        fetch_one=fetch_one,
        process_one=process or _Recorder(),
        make_source=make_source or (lambda enqueue, stop: _NullSource()),
    )
    watcher._fetch_pending_calls = fetch_pending_calls  # type: ignore[attr-defined]
    return watcher


class _NullSource:
    def start(self):
        pass

    def stop(self):
        pass


# ── tests ────────────────────────────────────────────────────────────────────
def test_startup_catch_up_processes_existing_once():
    """Jobs already in 'prefilling' are processed exactly once on launch."""
    rec = _Recorder()
    pending = [_job("a"), _job("b")]
    w = _make_watcher(pending=pending, process=rec)
    ctx = object()

    w._catch_up()
    w.drain(ctx)

    assert rec.calls == ["a", "b"]
    # Draining again with nothing new enqueued is a no-op (no double-processing).
    assert w.drain(ctx) == 0
    assert rec.calls == ["a", "b"]


def test_realtime_event_triggers_prepare():
    """A simulated UPDATE-to-prefilling event hands the job to process_one."""
    rec = _Recorder()
    w = _make_watcher(process=rec)
    ctx = object()

    # This is exactly what RealtimeEventSource._on_change does with a payload.
    payload = {"data": {"record": {"id": "evt-1", "status": "prefilling"}}}
    jid = extract_job_id(payload)
    w._enqueue(jid)
    w.drain(ctx)

    assert rec.calls == ["evt-1"]


def test_reconnect_reruns_catch_up():
    """A _CATCHUP sentinel (emitted on every (re)connect) re-scans prefilling."""
    rec = _Recorder()
    pending: list = []
    w = _make_watcher(pending=pending, process=rec)
    ctx = object()

    # First connect: nothing queued.
    w._enqueue(_CATCHUP)
    w.drain(ctx)
    assert rec.calls == []

    # A row gets queued while we were "disconnected"; reconnect → CATCHUP sweeps it.
    pending.append(_job("late"))
    w._enqueue(_CATCHUP)
    w.drain(ctx)

    assert rec.calls == ["late"]
    assert w._fetch_pending_calls["n"] == 2  # catch-up ran on each CATCHUP


def test_poll_fallback_processes_prefilling_jobs():
    """--poll: each tick emits CATCHUP, which the consumer turns into work."""
    rec = _Recorder()
    pending = [_job("p1")]
    enqueued: list = []

    def make_source(enqueue, stop):
        # Capture the real enqueue so we can drive one tick deterministically.
        enqueued.append(enqueue)
        return PollEventSource(enqueue, stop, interval=15, sleep=lambda s: None)

    w = _make_watcher(pending=pending, process=rec, make_source=make_source)
    ctx = object()

    source = w._make_source(w._enqueue, w._stop)
    source._tick()  # one poll cycle, no threads
    w.drain(ctx)

    assert rec.calls == ["p1"]


def test_one_job_at_a_time_and_dedup():
    """Strictly sequential, and the same id enqueued twice runs once."""
    rec = _Recorder()
    w = _make_watcher(process=rec)
    ctx = object()

    w._enqueue("x")
    w._enqueue("x")  # duplicate while in-flight → ignored
    w._enqueue("y")
    w.drain(ctx)

    assert rec.calls == ["x", "y"]
    assert rec.max_concurrency == 1


def test_skips_rows_that_left_prefilling():
    """If a row advanced between enqueue and processing, it is skipped."""
    rec = _Recorder()
    rows = {"gone": _job("gone", status="applied")}
    w = _make_watcher(rows=rows, process=rec)
    ctx = object()

    w._enqueue("gone")
    w.drain(ctx)

    assert rec.calls == []


def test_process_one_exception_does_not_kill_loop():
    """A failing job is logged and dropped; later jobs still process."""
    seen: list = []

    def flaky(job, context):
        seen.append(job["id"])
        if job["id"] == "boom":
            raise RuntimeError("adapter blew up")

    w = _make_watcher(process=flaky)
    ctx = object()
    w._enqueue("boom")
    w._enqueue("ok")
    w.drain(ctx)

    assert seen == ["boom", "ok"]
    # Both cleared from in-flight tracking despite the exception.
    assert w._active == set()


def test_run_lifecycle_opens_and_closes_context():
    """run(): opens the context once, catches up, then closes on stop."""
    rec = _Recorder()
    closed = {"v": False}
    started = {"v": False}

    def open_context():
        return object(), (lambda: closed.__setitem__("v", True))

    class _ImmediateSource:
        def __init__(self, enqueue, stop):
            self._stop = stop

        def start(self):
            started["v"] = True
            self._stop.set()  # ask the consumer to exit immediately

        def stop(self):
            pass

    w = SubmitWatcher(
        open_context=open_context,
        fetch_pending=lambda: [_job("boot")],
        fetch_one=lambda jid: _job(jid),
        process_one=rec,
        make_source=lambda enqueue, stop: _ImmediateSource(enqueue, stop),
        consumer_poll_timeout=0.01,
    )
    w.run()

    assert started["v"] is True
    assert closed["v"] is True
    assert rec.calls == ["boot"]  # startup catch-up ran before stop


# ── extract_job_id payload shapes ────────────────────────────────────────────
@pytest.mark.parametrize(
    "payload, expected",
    [
        ({"data": {"record": {"id": "r1"}}}, "r1"),          # realtime-py UPDATE
        ({"data": {"new": {"id": 42}}}, "42"),               # alt key, int id
        ({"record": {"id": "r2"}}, "r2"),                    # no data wrapper
        ({"id": "bare"}, "bare"),                            # bare row
        ({"data": {"record": {}}}, None),                    # record without id
        ({"data": {}}, None),                                # no record
        ({}, None),
    ],
)
def test_extract_job_id_shapes(payload, expected):
    assert extract_job_id(payload) == expected


# ── channel="chrome" browser wiring ──────────────────────────────────────────
class _FakeCtx:
    def close(self):
        pass


class _FakeChromium:
    def __init__(self):
        self.persistent_kwargs = None

    def launch_persistent_context(self, **kwargs):
        self.persistent_kwargs = kwargs
        return _FakeCtx()


class _FakePW:
    def __init__(self):
        self.chromium = _FakeChromium()


def test_browser_channel_threads_chrome_through(monkeypatch, tmp_path):
    """JOBPIPE_BROWSER_CHANNEL=chrome → launch_persistent_context(channel='chrome')."""
    from jobpipe.submit.browser import local

    monkeypatch.delenv("JOBPIPE_BROWSER_CDP", raising=False)
    monkeypatch.setenv("JOBPIPE_BROWSER_PROFILE", str(tmp_path / "profile"))
    monkeypatch.setenv("JOBPIPE_BROWSER_CHANNEL", "chrome")

    pw = _FakePW()
    context, closer = local.open_browser_context(pw, headless=False)

    assert pw.chromium.persistent_kwargs["channel"] == "chrome"
    assert pw.chromium.persistent_kwargs["headless"] is False
    assert callable(closer)


def test_browser_channel_omitted_when_unset(monkeypatch, tmp_path):
    """No channel env → no channel kwarg (bundled Chromium, the default)."""
    from jobpipe.submit.browser import local

    monkeypatch.delenv("JOBPIPE_BROWSER_CDP", raising=False)
    monkeypatch.delenv("JOBPIPE_BROWSER_CHANNEL", raising=False)
    monkeypatch.setenv("JOBPIPE_BROWSER_PROFILE", str(tmp_path / "profile"))

    pw = _FakePW()
    local.open_browser_context(pw, headless=False)

    assert "channel" not in pw.chromium.persistent_kwargs


# ── RealtimeEventSource unit (no network) ────────────────────────────────────
def test_realtime_on_change_enqueues_job_id():
    """_on_change extracts the id and enqueues it; junk payloads are ignored."""
    got: list = []
    src = RealtimeEventSource(
        lambda item: got.append(item),
        threading.Event(),
        url="https://x.supabase.co",
        token="svc",
    )
    src._on_change({"data": {"record": {"id": "rt-9", "status": "prefilling"}}})
    src._on_change({"data": {"record": {}}})  # no id → ignored

    assert got == ["rt-9"]


# ── RealtimeSubscriptionGuard (migration-014 gap detection, hygiene #3) ──────

def test_guard_does_not_warn_before_started():
    guard = RealtimeSubscriptionGuard(timeout_seconds=15.0)
    assert guard.should_warn(1000.0) is False


def test_guard_does_not_warn_before_timeout_elapses():
    guard = RealtimeSubscriptionGuard(timeout_seconds=15.0)
    guard.mark_started(0.0)
    assert guard.should_warn(10.0) is False


def test_guard_warns_once_after_timeout_with_no_subscribe():
    guard = RealtimeSubscriptionGuard(timeout_seconds=15.0)
    guard.mark_started(0.0)
    assert guard.should_warn(20.0) is True
    # Second call past the same window must not re-warn.
    assert guard.should_warn(30.0) is False


def test_guard_never_warns_once_subscribed():
    """The publication gap is exactly the case where subscribe would never
    confirm — a guard that DID subscribe must never warn, even much later."""
    guard = RealtimeSubscriptionGuard(timeout_seconds=15.0)
    guard.mark_started(0.0)
    guard.mark_subscribed(5.0)
    assert guard.should_warn(1000.0) is False


def test_guard_reconnect_after_subscribe_does_not_rewarn():
    """A guard instance persists across reconnects (one per process) — once
    it has ever subscribed, a later reconnect attempt's own slow handshake
    must not trigger a fresh warning cycle within the same guard."""
    guard = RealtimeSubscriptionGuard(timeout_seconds=15.0)
    guard.mark_started(0.0)
    guard.mark_subscribed(5.0)
    guard.mark_started(100.0)  # a later reconnect attempt starts again
    assert guard.should_warn(200.0) is False
