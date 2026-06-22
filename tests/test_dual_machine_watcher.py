"""tests/test_dual_machine_watcher.py — dual-machine submit-watcher coordination
(feat/dual-machine-watcher).

All mocked: no real browser, Supabase, or network. Covers:
  * WatcherCoordinator: active / dormant / unset decisions + heartbeats.
  * SubmitWatcher gated by a coordinate callback (active claims, dormant doesn't).
  * Heartbeat written each poll cycle; in-progress job finishes through a flip.
  * db helpers set_active_watcher_id / get_active_watcher_id round-trip and
    record_heartbeat upsert payload, over a chainable Supabase double.
  * CLI --set-active / --who-is-active wiring in run_submit_only.
"""

from __future__ import annotations

import logging
from unittest.mock import MagicMock

import pytest

from jobpipe.submit.watch import SubmitWatcher, WatcherCoordinator, _CATCHUP


# ── helpers ──────────────────────────────────────────────────────────────────
class _Recorder:
    def __init__(self):
        self.calls: list[str] = []

    def __call__(self, job, context):
        self.calls.append(job["id"])


def _job(job_id, status="prefilling"):
    return {"id": job_id, "status": status, "company": "Acme"}


def _make_watcher(*, pending=None, process=None, coordinate=None):
    pending = pending if pending is not None else []
    return SubmitWatcher(
        open_context=lambda: (object(), lambda: None),
        fetch_pending=lambda: list(pending),
        fetch_one=lambda jid: _job(jid),
        process_one=process or _Recorder(),
        make_source=lambda enqueue, stop: None,
        coordinate=coordinate,
    )


# ── WatcherCoordinator ───────────────────────────────────────────────────────
def test_coordinator_active_machine_claims_and_beats_active():
    beats: list = []
    coord = WatcherCoordinator(
        watcher_id="macbook",
        get_active=lambda: "macbook",
        record_heartbeat=lambda wid, state: beats.append((wid, state)),
    )
    assert coord.should_claim() is True
    assert beats == [("macbook", "active")]


def test_coordinator_dormant_machine_does_not_claim_and_beats_dormant():
    beats: list = []
    coord = WatcherCoordinator(
        watcher_id="desktop",
        get_active=lambda: "macbook",  # someone else is active
        record_heartbeat=lambda wid, state: beats.append((wid, state)),
    )
    assert coord.should_claim() is False
    assert beats == [("desktop", "dormant")]


def test_coordinator_unset_is_dormant_with_one_time_info(caplog):
    beats: list = []
    coord = WatcherCoordinator(
        watcher_id="macbook",
        get_active=lambda: None,  # nobody active
        record_heartbeat=lambda wid, state: beats.append((wid, state)),
    )
    with caplog.at_level(logging.INFO, logger="submit.watch"):
        assert coord.should_claim() is False
        assert coord.should_claim() is False

    # Dormant both times; heartbeat written each cycle.
    assert beats == [("macbook", "dormant"), ("macbook", "dormant")]
    # The "no active watcher" guidance is logged exactly once (latched).
    info = [r for r in caplog.records if "No active watcher" in r.message]
    assert len(info) == 1
    assert "--set-active macbook" in caplog.text


def test_coordinator_read_failure_is_dormant_not_raising():
    beats: list = []

    def boom():
        raise RuntimeError("supabase down")

    coord = WatcherCoordinator(
        watcher_id="macbook",
        get_active=boom,
        record_heartbeat=lambda wid, state: beats.append((wid, state)),
    )
    # A transient read failure must not raise into the watch loop, and must NOT
    # let this machine claim (two machines acting is worse than neither).
    assert coord.should_claim() is False
    assert beats == [("macbook", "dormant")]


# ── SubmitWatcher gated by coordinate ────────────────────────────────────────
def test_active_watcher_claims_pending_jobs():
    rec = _Recorder()
    w = _make_watcher(pending=[_job("a"), _job("b")], process=rec,
                      coordinate=lambda: True)
    ctx = object()
    w._catch_up()
    w.drain(ctx)
    assert rec.calls == ["a", "b"]


def test_dormant_watcher_claims_nothing():
    rec = _Recorder()
    w = _make_watcher(pending=[_job("a"), _job("b")], process=rec,
                      coordinate=lambda: False)
    ctx = object()
    w._catch_up()
    handled = w.drain(ctx)
    assert rec.calls == []
    assert handled == 0  # nothing was ever enqueued


def test_dormant_watcher_ignores_realtime_job_event():
    """A realtime job-id event on a dormant machine is not claimed."""
    rec = _Recorder()
    w = _make_watcher(process=rec, coordinate=lambda: False)
    ctx = object()
    w._enqueue("evt-1")          # simulate a realtime UPDATE-to-prefilling
    w.drain(ctx)
    assert rec.calls == []
    assert w._active == set()    # cleared from in-flight tracking


def test_heartbeat_written_each_cycle_even_when_dormant():
    """coordinate() (which writes the heartbeat) runs once per poll cycle."""
    cycles = {"n": 0}

    def coordinate():
        cycles["n"] += 1
        return False  # dormant

    w = _make_watcher(pending=[_job("a")], coordinate=coordinate)
    ctx = object()
    # Each CATCHUP == one poll tick == one coordinate() call == one heartbeat.
    w._enqueue(_CATCHUP)
    w._enqueue(_CATCHUP)
    w.drain(ctx)
    assert cycles["n"] == 2


def test_in_progress_job_finishes_when_toggle_flips_mid_cycle():
    """Once claimed, a job completes even if the machine goes dormant mid-job.

    The gate is only on *claiming*; process_one runs to completion. Here the job
    itself flips the toggle to dormant while running, and we assert it still
    finished (no mid-call abort).
    """
    state = {"active": True}
    finished: list = []

    def process_one(job, context):
        state["active"] = False  # toggle flips to another machine mid-job
        finished.append(job["id"])

    w = _make_watcher(pending=[_job("a")], process=process_one,
                      coordinate=lambda: state["active"])
    ctx = object()
    w._catch_up()   # active → claims "a"
    w.drain(ctx)    # process_one runs, flips state, completes
    assert finished == ["a"]
    assert state["active"] is False


def test_no_coordinate_defaults_to_always_active():
    """Single-machine wiring (no coordinator) behaves as before — always claims."""
    rec = _Recorder()
    w = _make_watcher(pending=[_job("solo")], process=rec)  # coordinate=None
    ctx = object()
    w._catch_up()
    w.drain(ctx)
    assert rec.calls == ["solo"]


# ── db helpers round-trip (chainable Supabase double) ────────────────────────
class _FakeQuery:
    def __init__(self, store):
        self._store = store
        self._mode = None

    def select(self, _cols):
        self._mode = "select"
        return self

    def eq(self, _col, _val):
        return self

    def limit(self, _n):
        return self

    def order(self, _col, desc=False):
        return self

    def upsert(self, payload, on_conflict=None):
        self._mode = "upsert"
        self._store["last_upsert"] = payload
        self._store["last_on_conflict"] = on_conflict
        return self

    def execute(self):
        if self._mode == "select":
            return MagicMock(data=list(self._store.get("rows", [])))
        return MagicMock(data=[self._store.get("last_upsert", {})])


class _FakeClient:
    def __init__(self, store):
        self._store = store

    def table(self, name):
        self._store["last_table"] = name
        return _FakeQuery(self._store)


@pytest.fixture
def fake_db(monkeypatch):
    import jobpipe.db as db
    store: dict = {"rows": []}
    monkeypatch.setattr(db, "_client", _FakeClient(store))
    return db, store


def test_set_and_get_active_watcher_id_round_trip(fake_db):
    db, store = fake_db
    db.set_active_watcher_id("desktop")
    assert store["last_table"] == "watcher_config"
    assert store["last_upsert"]["active_watcher_id"] == "desktop"
    assert store["last_upsert"]["id"] is True
    assert store["last_on_conflict"] == "id"

    # Now make the select return what we "stored".
    store["rows"] = [{"active_watcher_id": "desktop"}]
    assert db.get_active_watcher_id() == "desktop"


def test_get_active_watcher_id_none_when_unset(fake_db):
    db, store = fake_db
    store["rows"] = []
    assert db.get_active_watcher_id() is None
    store["rows"] = [{"active_watcher_id": None}]
    assert db.get_active_watcher_id() is None


def test_record_heartbeat_upserts_on_watcher_id(fake_db):
    db, store = fake_db
    db.record_heartbeat("macbook", "active")
    assert store["last_table"] == "watcher_heartbeats"
    assert store["last_upsert"]["watcher_id"] == "macbook"
    assert store["last_upsert"]["state"] == "active"
    assert "last_seen" in store["last_upsert"]
    assert store["last_on_conflict"] == "watcher_id"


# ── CLI: --set-active / --who-is-active ──────────────────────────────────────
def test_cli_set_active_updates(monkeypatch, capsys):
    from jobpipe.tailor import pipeline
    captured: dict = {}
    monkeypatch.setattr(pipeline, "set_active_watcher_id",
                        lambda wid: captured.__setitem__("wid", wid))
    monkeypatch.setattr("sys.argv", ["jobpipe-submit", "--set-active", "desktop"])
    pipeline.run_submit_only()
    assert captured["wid"] == "desktop"
    assert "desktop" in capsys.readouterr().out


def test_cli_who_is_active_prints(monkeypatch, capsys):
    from jobpipe.tailor import pipeline
    monkeypatch.setattr(pipeline, "get_active_watcher_id", lambda: "macbook")
    monkeypatch.setattr(pipeline, "get_watcher_heartbeats",
                        lambda: [{"watcher_id": "macbook", "state": "active",
                                  "last_seen": "2026-06-22T00:00:00Z"}])
    monkeypatch.setattr("sys.argv", ["jobpipe-submit", "--who-is-active"])
    pipeline.run_submit_only()
    out = capsys.readouterr().out
    assert "active_watcher_id = macbook" in out
    assert "macbook" in out


def test_cli_set_active_no_value_prints_current(monkeypatch, capsys):
    from jobpipe.tailor import pipeline
    monkeypatch.setattr(pipeline, "get_active_watcher_id", lambda: "desktop")
    monkeypatch.setattr(pipeline, "get_watcher_heartbeats", lambda: [])
    # A bare --set-active (no value) prints rather than sets.
    called = {"set": False}
    monkeypatch.setattr(pipeline, "set_active_watcher_id",
                        lambda wid: called.__setitem__("set", True))
    monkeypatch.setattr("sys.argv", ["jobpipe-submit", "--set-active"])
    pipeline.run_submit_only()
    out = capsys.readouterr().out
    assert "active_watcher_id = desktop" in out
    assert called["set"] is False
