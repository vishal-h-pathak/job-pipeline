"""S6 — the workflow's ``JOBPIPE_RUN_ID`` threads into the agents' cost_context.

S2 hardcoded ``run_id=None`` in both agents, so every ``cost_events`` row landed
with ``run_id = NULL`` and ``rollup_run()`` summed nothing — ``runs.cost_usd``
stayed ``0``. S6 makes the hunt and tailor entry points read the env the GHA
workflows now pass (``hunt.yml`` / ``tailor.yml``) so the rows carry the run id
and the per-run rollup actually sums.

Covers:
  1. hunt ``run()`` and tailor ``process_one_approved_job`` open a
     ``cost_context`` whose ``run_id`` equals ``JOBPIPE_RUN_ID``, and a row
     recorded inside that context carries it.
  2. env unset (local runs, smoke tests) → ``run_id`` is ``None`` — no
     regression; rows stay stage/job-attributed.
  3. end-to-end: env set → events recorded through the recorder → ``rollup_run``
     sums them into ``runs.cost_usd`` (reuses the stubbed-Supabase double pattern
     from ``tests/test_cost_rollup.py``).
"""

from __future__ import annotations

import sys
from unittest.mock import MagicMock

import pytest

from jobpipe.shared import cost


# ── Chainable Supabase double (mirrors tests/test_cost_rollup.py) ────────────
# Enriched so a cost_events insert becomes a readable row and select honours
# .eq() filters — that makes the rollup leg of test (3) a genuine round trip:
# record → insert → select WHERE run_id=<id> → sum.
class _FakeQuery:
    def __init__(self, store):
        self._store = store
        self._mode = None
        self._filters: dict = {}

    def insert(self, payload):
        self._mode = "insert"
        table = self._store["last_table"]
        self._store.setdefault("inserts", []).append(
            {"table": table, "payload": payload}
        )
        # cost_events inserts become rows the rollup select can read back.
        if table == "cost_events":
            self._store.setdefault("rows", []).append(dict(payload))
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

    def eq(self, col, val):
        self._filters[col] = val
        return self

    def execute(self):
        if self._mode == "select":
            rows = list(self._store.get("rows", []))
            for col, val in self._filters.items():
                rows = [r for r in rows if r.get(col) == val]
            return MagicMock(data=rows)
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
    # db.service_client resolves to the module-level _service_client singleton
    # via db.__getattr__, so stubbing the singleton stubs the canonical client.
    monkeypatch.setattr(db, "_service_client", _FakeClient(store))
    return store


def _cost_event_inserts(store):
    return [i for i in store.get("inserts", []) if i["table"] == "cost_events"]


# ── 1. hunt threads JOBPIPE_RUN_ID into its cost_context ─────────────────────
def test_hunt_threads_run_id_when_env_set(fake_db, monkeypatch):
    import jobpipe.hunt.agent as agent

    captured: dict = {}

    def fake_execute():
        captured["ctx"] = dict(cost._current())
        # a real Anthropic call would record here; prove the row carries run_id
        cost.record_units("anthropic-test", {"calls": 1}, 0.5)

    monkeypatch.setattr(agent, "_execute", fake_execute)
    monkeypatch.setenv("JOBPIPE_RUN_ID", "run-hunt-1")
    monkeypatch.setattr(sys, "argv", ["jobpipe-hunt"])

    agent.run()

    assert captured["ctx"]["run_id"] == "run-hunt-1"
    assert captured["ctx"]["stage"] == "hunt"
    inserts = _cost_event_inserts(fake_db)
    assert inserts and inserts[-1]["payload"]["run_id"] == "run-hunt-1"


def test_hunt_run_id_none_when_env_unset(fake_db, monkeypatch):
    import jobpipe.hunt.agent as agent

    captured: dict = {}
    monkeypatch.setattr(
        agent, "_execute", lambda: captured.update(ctx=dict(cost._current()))
    )
    monkeypatch.delenv("JOBPIPE_RUN_ID", raising=False)
    monkeypatch.setattr(sys, "argv", ["jobpipe-hunt"])

    agent.run()

    assert captured["ctx"]["run_id"] is None
    assert captured["ctx"]["stage"] == "hunt"


# ── 2. tailor threads JOBPIPE_RUN_ID into its cost_context ───────────────────
def test_tailor_threads_run_id_when_env_set(fake_db, monkeypatch):
    import jobpipe.tailor.pipeline as pipeline

    captured: dict = {}

    def fake_inner(job_id):
        captured["ctx"] = dict(cost._current())
        cost.record_units("anthropic-test", {"calls": 1}, 0.25)

    monkeypatch.setattr(pipeline, "_process_one_approved_job", fake_inner)
    monkeypatch.setenv("JOBPIPE_RUN_ID", "run-tailor-1")

    pipeline.process_one_approved_job("job-xyz")

    assert captured["ctx"]["run_id"] == "run-tailor-1"
    assert captured["ctx"]["stage"] == "tailor"
    assert captured["ctx"]["job_id"] == "job-xyz"
    inserts = _cost_event_inserts(fake_db)
    assert inserts and inserts[-1]["payload"]["run_id"] == "run-tailor-1"


def test_tailor_run_id_none_when_env_unset(fake_db, monkeypatch):
    import jobpipe.tailor.pipeline as pipeline

    captured: dict = {}
    monkeypatch.setattr(
        pipeline,
        "_process_one_approved_job",
        lambda job_id: captured.update(ctx=dict(cost._current())),
    )
    monkeypatch.delenv("JOBPIPE_RUN_ID", raising=False)

    pipeline.process_one_approved_job("job-xyz")

    assert captured["ctx"]["run_id"] is None
    assert captured["ctx"]["stage"] == "tailor"
    assert captured["ctx"]["job_id"] == "job-xyz"


# ── 3. end-to-end: threaded run_id rolls up into runs.cost_usd ───────────────
def test_runid_thread_rolls_up_end_to_end(fake_db, monkeypatch):
    """env set → recorded events carry run_id → rollup_run sums them."""
    import jobpipe.hunt.agent as agent

    store = fake_db
    monkeypatch.setenv("JOBPIPE_RUN_ID", "run-e2e")

    def fake_execute():
        cost.record_units("serpapi", {"calls": 1}, 0.10)
        cost.record_units("anthropic", {"tok": 1}, 0.40)

    monkeypatch.setattr(agent, "_execute", fake_execute)
    monkeypatch.setattr(sys, "argv", ["jobpipe-hunt"])

    agent.run()

    # both events landed tagged with the threaded run id
    inserts = _cost_event_inserts(store)
    assert len(inserts) == 2
    assert all(i["payload"]["run_id"] == "run-e2e" for i in inserts)

    # rollup sums WHERE run_id = 'run-e2e' into runs.cost_usd
    cost.rollup_run("run-e2e")
    runs = [u for u in store.get("updates", []) if u["table"] == "runs"]
    assert len(runs) == 1
    assert runs[-1]["payload"]["cost_usd"] == pytest.approx(0.50)


def test_runid_thread_does_not_cross_runs(fake_db, monkeypatch):
    """A different run's events are excluded from a run's rollup."""
    import jobpipe.hunt.agent as agent

    store = fake_db

    def make_execute(amount):
        def _exec():
            cost.record_units("anthropic", {"tok": 1}, amount)
        return _exec

    monkeypatch.setattr(sys, "argv", ["jobpipe-hunt"])

    monkeypatch.setenv("JOBPIPE_RUN_ID", "run-A")
    monkeypatch.setattr(agent, "_execute", make_execute(1.00))
    agent.run()

    monkeypatch.setenv("JOBPIPE_RUN_ID", "run-B")
    monkeypatch.setattr(agent, "_execute", make_execute(2.00))
    agent.run()

    cost.rollup_run("run-A")
    runs = [u for u in store.get("updates", []) if u["table"] == "runs"]
    assert runs[-1]["payload"]["cost_usd"] == pytest.approx(1.00)
