"""S3 straggler instrumentation — interview-prep + submit prepare-loop.

These two call sites issue Anthropic calls via the SDK directly (they do
not route through ``jobpipe.shared.llm``), so each grew its own
``cost.record_anthropic`` capture. The tests monkeypatch the SDK so no
network/credentials are needed and assert a ``cost_events`` row lands with
the right ``stage``. The prepare-loop test drives ≥2 fake turns and asserts
one row per turn (per-turn recording = accumulation across the run).

Reuses the chainable Supabase double from ``tests/test_cost_recorder.py``
(stub ``db._service_client``) so the recorder's real pricing + context
path runs end to end.
"""

from __future__ import annotations

import os

# ``jobpipe.submit.config`` fail-louds on these secrets at import time, which
# happens during collection when we import ``prepare_loop`` below. Provide
# harmless test defaults — ``setdefault`` never overrides real CI secrets — so
# collection succeeds even without a ``.env`` present.
for _k, _v in {
    "SUPABASE_URL": "https://example.supabase.co",
    "SUPABASE_KEY": "anon-test",
    "SUPABASE_SERVICE_ROLE_KEY": "service-test",
    "ANTHROPIC_API_KEY": "sk-test",
}.items():
    os.environ.setdefault(_k, _v)

from types import SimpleNamespace  # noqa: E402
from unittest.mock import MagicMock  # noqa: E402

import pytest  # noqa: E402

from jobpipe.shared import cost  # noqa: E402
from jobpipe.tailor import pipeline  # noqa: F401,E402 — fires the tailor sys.path bootstrap

from interview_prep import generator as stories_mod  # noqa: E402 — needs bootstrap above
from jobpipe.submit.adapters import prepare_loop  # noqa: E402


# ── Chainable Supabase double (captures inserts) ─────────────────────────
class _FakeQuery:
    def __init__(self, store):
        self._store = store

    def insert(self, payload):
        self._store.setdefault("inserts", []).append(
            {"table": self._store["last_table"], "payload": payload}
        )
        return self

    def execute(self):
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
    store: dict = {}
    monkeypatch.setattr(db, "_service_client", _FakeClient(store))
    return store


@pytest.fixture(autouse=True)
def _reset_context():
    token = cost._COST_CONTEXT.set({})
    yield
    cost._COST_CONTEXT.reset(token)


def _cost_rows(store):
    return [i["payload"] for i in store.get("inserts", []) if i["table"] == "cost_events"]


def _usage(inp=0, out=0):
    return SimpleNamespace(
        input_tokens=inp,
        output_tokens=out,
        cache_read_input_tokens=0,
        cache_creation_input_tokens=0,
    )


# ── interview-prep ───────────────────────────────────────────────────────
class _Block:
    def __init__(self, text):
        self.text = text


class _IPrepMessages:
    def __init__(self, resp):
        self._resp = resp

    def create(self, **kwargs):
        return self._resp


class _IPrepClient:
    def __init__(self, resp):
        self.messages = _IPrepMessages(resp)


def test_interview_prep_records_cost(fake_db, monkeypatch):
    store = fake_db
    resp = SimpleNamespace(
        content=[_Block('{"stories": []}')],
        usage=_usage(inp=1_000, out=500),
    )
    monkeypatch.setattr(stories_mod, "_client_lazy", lambda: _IPrepClient(resp))

    stories_mod.generate_stories(
        {"id": "job-iprep", "title": "Research Eng", "company": "Co", "description": "x"},
        archetype_meta={"archetype": "tier_1a_compneuro"},
    )

    rows = _cost_rows(store)
    assert len(rows) == 1
    row = rows[0]
    assert row["stage"] == "interview_prep"
    assert row["service"] == "anthropic"
    assert row["auth_path"] == "api_key"
    assert row["model"] == stories_mod.CLAUDE_MODEL
    assert row["job_id"] == "job-iprep"
    assert row["units"]["input_tokens"] == 1_000
    assert row["units"]["output_tokens"] == 500


# ── submit prepare-loop (multi-turn accumulation) ────────────────────────
class _ToolUse:
    type = "tool_use"

    def __init__(self, tid, name, inp):
        self.id = tid
        self.name = name
        self.input = inp


def _turn_resp(usage):
    return SimpleNamespace(
        content=[_ToolUse("tu", "screenshot", {})],
        usage=usage,
        stop_reason="tool_use",
    )


class _SeqMessages:
    def __init__(self, responses):
        self._responses = list(responses)
        self.calls: list[dict] = []

    def create(self, **kwargs):
        self.calls.append(kwargs)
        return self._responses.pop(0)


class _SeqClient:
    def __init__(self, responses):
        self.messages = _SeqMessages(responses)


class _FakeSession:
    def __init__(self):
        self.finished = False
        self.needs_review = False
        self.review_reason = None
        self.review_uncertain = []
        self.screenshots = []
        self.filled_fields = {}
        self.page = SimpleNamespace(url="https://example.invalid/form")


def test_prepare_loop_accumulates_across_turns(fake_db, monkeypatch):
    store = fake_db

    # Two turns of fake usage; per-turn recording => one row each.
    responses = [_turn_resp(_usage(inp=100, out=10)), _turn_resp(_usage(inp=200, out=20))]
    seq = _SeqClient(responses)

    monkeypatch.setattr(prepare_loop, "ANTHROPIC_API_KEY", "sk-test")
    monkeypatch.setattr(
        prepare_loop, "anthropic", SimpleNamespace(Anthropic=lambda **kw: seq)
    )
    # Avoid real profile / prompt file loads.
    monkeypatch.setattr(prepare_loop, "_load_profile", lambda: "profile")
    monkeypatch.setattr(prepare_loop, "_load_voice_profile", lambda: "voice")
    monkeypatch.setattr(prepare_loop, "_format_form_answers_block", lambda *a, **k: "")
    monkeypatch.setattr(prepare_loop, "load_prompt", lambda *a, **k: "system")

    # Finish the loop after the 2nd turn's tool runs, so create() fires twice.
    counter = {"n": 0}

    def fake_run_tool(session, name, inp):
        counter["n"] += 1
        if counter["n"] >= 2:
            session.finished = True
        return ("ok", False)

    monkeypatch.setattr(prepare_loop, "_run_tool", fake_run_tool)

    session = _FakeSession()
    prepare_loop.run_submission_agent(session, {"job_id": "sub-1"})

    rows = _cost_rows(store)
    assert len(rows) == 2  # one per turn — accumulation across the run
    assert all(r["stage"] == "submit" for r in rows)
    assert all(r["service"] == "anthropic" and r["auth_path"] == "api_key" for r in rows)
    assert all(r["job_id"] == "sub-1" for r in rows)
    # The run's token total is the sum across turns.
    assert sum(r["units"]["input_tokens"] for r in rows) == 300
    assert sum(r["units"]["output_tokens"] for r in rows) == 30
