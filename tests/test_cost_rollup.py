"""S3 end-of-run rollup — ``cost.rollup_run`` + the ``mark_run`` finalize path.

When a run is marked ``completed`` (``scripts/mark_run.py``, called by both
hunt.yml and tailor.yml at the bottom of a successful run), S3 sums that
run's ``cost_events.cost_usd`` into the denormalized ``runs.cost_usd`` so
the dashboard ledger has a single number per run.

Two layers tested:
  1. ``cost.rollup_run`` sums seeded events and writes the rollup.
  2. ``mark_run.py <id> completed`` triggers that rollup (the S3 wiring).
"""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path
from unittest.mock import MagicMock

import pytest

from jobpipe.shared import cost


def _load_mark_run():
    """Load ``scripts/mark_run.py`` by path (it's a script, not a package)."""
    path = Path(__file__).resolve().parent.parent / "scripts" / "mark_run.py"
    spec = importlib.util.spec_from_file_location("mark_run_under_test", path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


# ── Chainable Supabase double (select returns seeded rows; captures updates) ──
class _FakeQuery:
    def __init__(self, store):
        self._store = store
        self._mode = None

    def insert(self, payload):
        self._mode = "insert"
        self._store.setdefault("inserts", []).append(
            {"table": self._store["last_table"], "payload": payload}
        )
        return self

    def select(self, _cols):
        self._mode = "select"
        return self

    def update(self, payload):
        self._mode = "update"
        self._store.setdefault("updates", []).append(
            {"table": self._store["last_table"], "payload": payload}
        )
        return self

    def eq(self, _col, _val):
        return self

    def execute(self):
        if self._mode == "select":
            return MagicMock(data=list(self._store.get("rows", [])))
        return MagicMock(data=[{}])


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
    monkeypatch.setattr(db, "_service_client", _FakeClient(store))
    return store


def _run_updates(store):
    return [u for u in store.get("updates", []) if u["table"] == "runs"]


# ── rollup_run ───────────────────────────────────────────────────────────
def test_rollup_run_sums_seeded_events(fake_db):
    store = fake_db
    store["rows"] = [{"cost_usd": 0.5}, {"cost_usd": 1.25}, {"cost_usd": 0.30}]

    cost.rollup_run("run-42")

    runs = _run_updates(store)
    assert len(runs) == 1
    assert runs[0]["payload"]["cost_usd"] == pytest.approx(2.05)


def test_rollup_run_no_events_is_zero(fake_db):
    store = fake_db
    store["rows"] = []
    cost.rollup_run("run-empty")
    assert _run_updates(store)[0]["payload"]["cost_usd"] == 0


# ── mark_run finalize path ───────────────────────────────────────────────
def test_mark_run_completed_triggers_rollup(fake_db, monkeypatch):
    mark_run = _load_mark_run()

    store = fake_db
    store["rows"] = [{"cost_usd": 3.0}, {"cost_usd": 1.0}]

    monkeypatch.setattr(sys, "argv", ["mark_run.py", "run-99", "completed"])
    rc = mark_run.main()
    assert rc == 0

    runs = _run_updates(store)
    # Two runs updates: the status->completed mark, then the cost rollup.
    cost_updates = [u for u in runs if "cost_usd" in u["payload"]]
    assert len(cost_updates) == 1
    assert cost_updates[0]["payload"]["cost_usd"] == pytest.approx(4.0)


def test_mark_run_running_does_not_roll_up(fake_db, monkeypatch):
    mark_run = _load_mark_run()

    store = fake_db
    store["rows"] = [{"cost_usd": 3.0}]

    monkeypatch.setattr(sys, "argv", ["mark_run.py", "run-99", "running"])
    assert mark_run.main() == 0

    cost_updates = [u for u in _run_updates(store) if "cost_usd" in u["payload"]]
    assert cost_updates == []
