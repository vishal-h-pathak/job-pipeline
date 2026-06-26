"""Unit tests for jobpipe.shared.cost — the guarded cost-event recorder.

Asserts the recorder writes one well-shaped ``cost_events`` row pulling
attribution from the ``cost_context`` contextvar, zeroes cost on the
OAuth path while still recording units, and — critically — swallows any
DB error so telemetry can never fail or slow the pipeline.

Uses the same chainable Supabase double pattern as
``tests/test_dual_machine_watcher.py``, stubbing ``db._service_client``.
"""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

from jobpipe.shared import cost


# ── Chainable Supabase double (captures inserts / updates) ───────────────
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


class _RaisingClient:
    def table(self, name):
        raise RuntimeError("supabase is down")


@pytest.fixture
def fake_db(monkeypatch):
    import jobpipe.db as db
    store: dict = {"rows": []}
    monkeypatch.setattr(db, "_service_client", _FakeClient(store))
    return db, store


@pytest.fixture(autouse=True)
def _reset_context():
    """Each test starts with an empty attribution context."""
    token = cost._COST_CONTEXT.set({})
    yield
    cost._COST_CONTEXT.reset(token)


def _usage(inp=0, out=0, cache_read=0, cache_creation=0):
    return SimpleNamespace(
        input_tokens=inp,
        output_tokens=out,
        cache_read_input_tokens=cache_read,
        cache_creation_input_tokens=cache_creation,
    )


# ── cost_context ─────────────────────────────────────────────────────────

def test_cost_context_sets_and_resets():
    assert cost._current() == {}
    with cost.cost_context(run_id="r1", stage="hunt"):
        assert cost._current()["run_id"] == "r1"
        assert cost._current()["stage"] == "hunt"
    assert cost._current() == {}


def test_cost_context_nests_and_merges():
    with cost.cost_context(run_id="r1", stage="tailor"):
        with cost.cost_context(job_id="j9"):
            ctx = cost._current()
            assert ctx == {"run_id": "r1", "stage": "tailor", "job_id": "j9"}
        # inner context popped; outer intact
        assert "job_id" not in cost._current()
        assert cost._current()["stage"] == "tailor"


# ── record_anthropic ─────────────────────────────────────────────────────

def test_record_anthropic_writes_one_row(fake_db):
    db, store = fake_db
    with cost.cost_context(run_id="run-1", stage="hunt", job_id="job-7"):
        cost.record_anthropic(
            "claude-sonnet-4-6",
            _usage(inp=1_000_000, out=1_000_000),
            "api_key",
        )
    inserts = store["inserts"]
    assert len(inserts) == 1
    row = inserts[0]["payload"]
    assert inserts[0]["table"] == "cost_events"
    assert row["service"] == "anthropic"
    assert row["model"] == "claude-sonnet-4-6"
    assert row["stage"] == "hunt"
    assert row["run_id"] == "run-1"
    assert row["job_id"] == "job-7"
    assert row["auth_path"] == "api_key"
    assert row["units"] == {
        "input_tokens": 1_000_000,
        "output_tokens": 1_000_000,
        "cache_read": 0,
        "cache_creation": 0,
    }
    # Sonnet $3 in + $15 out for a full Mtok each.
    assert row["cost_usd"] == pytest.approx(18.0)


def test_record_anthropic_includes_cache_units(fake_db):
    db, store = fake_db
    with cost.cost_context(stage="tailor"):
        cost.record_anthropic(
            "claude-opus-4-8",
            _usage(inp=0, out=0, cache_read=1_000_000, cache_creation=0),
            "api_key",
        )
    row = store["inserts"][0]["payload"]
    assert row["units"]["cache_read"] == 1_000_000
    # cache_read billed at 10% of $5 input rate.
    assert row["cost_usd"] == pytest.approx(0.5)


def test_record_anthropic_oauth_zeroes_cost_but_keeps_units(fake_db):
    db, store = fake_db
    with cost.cost_context(stage="hunt"):
        cost.record_anthropic(
            "claude-sonnet-4-6",
            _usage(inp=1_000_000, out=1_000_000),
            "oauth",
        )
    row = store["inserts"][0]["payload"]
    assert row["auth_path"] == "oauth"
    assert row["cost_usd"] == 0
    # Units still recorded so the call stays visible.
    assert row["units"]["input_tokens"] == 1_000_000


def test_record_anthropic_swallows_db_error(monkeypatch):
    import jobpipe.db as db
    monkeypatch.setattr(db, "_service_client", _RaisingClient())
    # Must not raise.
    with cost.cost_context(stage="hunt"):
        cost.record_anthropic("claude-opus-4-8", _usage(inp=10), "api_key")


def test_record_anthropic_unknown_model_records_zero(fake_db):
    db, store = fake_db
    with cost.cost_context(stage="hunt"):
        cost.record_anthropic("mystery-model", _usage(inp=1_000_000), "api_key")
    row = store["inserts"][0]["payload"]
    assert row["cost_usd"] == 0
    assert row["units"]["input_tokens"] == 1_000_000


# ── record_units ─────────────────────────────────────────────────────────

def test_record_units_writes_generic_row(fake_db):
    db, store = fake_db
    with cost.cost_context(run_id="run-2", stage="hunt"):
        cost.record_units(
            "jsearch", {"requests": 3}, cost.pricing.JSEARCH_USD_PER_REQUEST * 3
        )
    row = store["inserts"][0]["payload"]
    assert row["service"] == "jsearch"
    assert row["model"] is None
    assert row["stage"] == "hunt"
    assert row["run_id"] == "run-2"
    assert row["units"] == {"requests": 3}
    assert row["cost_usd"] == pytest.approx(10 / 1500 * 3)


def test_record_units_stage_override(fake_db):
    db, store = fake_db
    with cost.cost_context(stage="hunt"):
        cost.record_units("serpapi", {"searches": 5}, 0.0, stage="discovery")
    row = store["inserts"][0]["payload"]
    assert row["stage"] == "discovery"


def test_record_units_swallows_db_error(monkeypatch):
    import jobpipe.db as db
    monkeypatch.setattr(db, "_service_client", _RaisingClient())
    cost.record_units("serpapi", {"searches": 1}, 0.0)  # must not raise


# ── rollup_run ───────────────────────────────────────────────────────────

def test_rollup_run_sums_and_updates(fake_db):
    db, store = fake_db
    store["rows"] = [
        {"cost_usd": 1.25},
        {"cost_usd": 2.50},
        {"cost_usd": 0.25},
    ]
    cost.rollup_run("run-9")
    updates = store["updates"]
    assert len(updates) == 1
    assert updates[0]["table"] == "runs"
    assert updates[0]["payload"]["cost_usd"] == pytest.approx(4.0)


def test_rollup_run_empty_is_zero(fake_db):
    db, store = fake_db
    store["rows"] = []
    cost.rollup_run("run-10")
    assert store["updates"][0]["payload"]["cost_usd"] == 0


def test_rollup_run_swallows_db_error(monkeypatch):
    import jobpipe.db as db
    monkeypatch.setattr(db, "_service_client", _RaisingClient())
    cost.rollup_run("run-x")  # must not raise
