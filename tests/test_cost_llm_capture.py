"""tests/test_cost_llm_capture.py — cost capture at the llm.complete() chokepoint.

S2 of the cost tracker: `jobpipe.shared.llm.complete()` is the single
highest-value instrumentation point (the hunt scorer + all five tailor
modules route through it). These tests assert the side-effect capture:

  - API-key branch: after a successful Messages API call, one
    ``cost_events`` row is recorded with the stage from the active
    ``cost_context``, ``auth_path="api_key"``, and the priced dollar cost.
  - OAuth fallback: when the key is benched (cool-off) the OAuth path is
    taken and the call is recorded at ``cost_usd=0`` (subscription) while
    still keeping the token counts visible.

Fully mocked: no network, no real Anthropic client, no real Supabase. The
recorder's Supabase write is redirected to an in-memory double (the same
chainable pattern as ``tests/test_cost_recorder.py``); the OAuth SDK is a
fake module injected into ``sys.modules`` (mirrors ``test_shared_llm.py``).
"""

from __future__ import annotations

import sys
import types
from types import SimpleNamespace

import pytest

from jobpipe.shared import cost, llm


# ── In-memory Supabase double (captures cost_events inserts) ───────────────
class _FakeQuery:
    def __init__(self, store):
        self._store = store

    def insert(self, payload):
        self._store.setdefault("inserts", []).append(
            {"table": self._store["last_table"], "payload": payload}
        )
        return self

    def execute(self):
        return SimpleNamespace(data=[{}])


class _FakeClient:
    def __init__(self, store):
        self._store = store

    def table(self, name):
        self._store["last_table"] = name
        return _FakeQuery(self._store)


@pytest.fixture
def cost_store(monkeypatch):
    """Redirect the recorder's service client to an in-memory capture."""
    import jobpipe.db as db
    store: dict = {}
    monkeypatch.setattr(db, "_service_client", _FakeClient(store))
    return store


@pytest.fixture(autouse=True)
def _reset_state(monkeypatch):
    """Fresh (un-benched) key and empty attribution context per test."""
    monkeypatch.setattr(llm, "_api_key_cool_off_until", 0.0)
    token = cost._COST_CONTEXT.set({})
    yield
    cost._COST_CONTEXT.reset(token)


# ── Fakes for the API path ─────────────────────────────────────────────────
class _FakeBlock:
    def __init__(self, text):
        self.text = text


class _FakeResp:
    """Messages API response carrying content blocks + a usage object."""

    def __init__(self, text, usage):
        self.content = [_FakeBlock(text)]
        self.usage = usage


class _RecordingClient:
    def __init__(self, resp):
        class _Messages:
            def create(self, **kwargs):  # noqa: ARG002 — shape match
                return resp

        self.messages = _Messages()


def _usage(inp=0, out=0, cache_read=0, cache_creation=0):
    return SimpleNamespace(
        input_tokens=inp,
        output_tokens=out,
        cache_read_input_tokens=cache_read,
        cache_creation_input_tokens=cache_creation,
    )


# ── API-key path records a priced row ──────────────────────────────────────
def test_api_path_records_cost_event_with_context_stage(monkeypatch, cost_store):
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-live")
    resp = _FakeResp("scored", _usage(inp=1_000_000, out=1_000_000))
    monkeypatch.setattr(llm, "_anthropic_client", lambda *a, **k: _RecordingClient(resp))

    def _no_oauth(**kwargs):
        raise AssertionError("OAuth path must not run while credits work")

    monkeypatch.setattr(llm, "_oauth_complete", _no_oauth)

    with cost.cost_context(run_id="run-llm", stage="hunt", job_id="job-3"):
        out = llm.complete(
            system="SYS", prompt="hi",
            model="claude-sonnet-4-6", max_tokens=128,
        )

    # Return type/value unchanged — capture is a side effect only.
    assert out == "scored"

    inserts = cost_store.get("inserts", [])
    assert len(inserts) == 1
    row = inserts[0]["payload"]
    assert inserts[0]["table"] == "cost_events"
    assert row["service"] == "anthropic"
    assert row["model"] == "claude-sonnet-4-6"
    assert row["auth_path"] == "api_key"
    assert row["stage"] == "hunt"
    assert row["run_id"] == "run-llm"
    assert row["job_id"] == "job-3"
    assert row["units"] == {
        "input_tokens": 1_000_000,
        "output_tokens": 1_000_000,
        "cache_read": 0,
        "cache_creation": 0,
    }
    # Sonnet: $3/Mtok in + $15/Mtok out, one full Mtok each.
    assert row["cost_usd"] == pytest.approx(18.0)


def test_api_path_capture_failure_never_breaks_complete(monkeypatch):
    """A telemetry write blowing up must not change complete()'s result."""
    import jobpipe.db as db

    class _Boom:
        def table(self, name):
            raise RuntimeError("supabase down")

    monkeypatch.setattr(db, "_service_client", _Boom())
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-live")
    resp = _FakeResp("still works", _usage(inp=10, out=5))
    monkeypatch.setattr(llm, "_anthropic_client", lambda *a, **k: _RecordingClient(resp))

    with cost.cost_context(stage="hunt"):
        out = llm.complete(
            system="SYS", prompt="hi",
            model="claude-sonnet-4-6", max_tokens=64,
        )
    assert out == "still works"


# ── OAuth fallback records a $0 row (key benched into cool-off) ─────────────
def _install_fake_sdk(monkeypatch, *, messages):
    mod = types.ModuleType("claude_agent_sdk")
    state: dict = {"messages": messages}

    class TextBlock:
        def __init__(self, text):
            self.text = text

    class AssistantMessage:
        def __init__(self, content):
            self.content = content

    class ResultMessage:
        def __init__(self, subtype, is_error=False, result=None, usage=None):
            self.subtype = subtype
            self.is_error = is_error
            self.result = result
            self.usage = usage

    class ClaudeAgentOptions:
        def __init__(self, **kwargs):
            state["options"] = kwargs

    async def query(*, prompt, options):  # noqa: ARG001 — signature match
        for msg in state["messages"]:
            yield msg

    mod.TextBlock = TextBlock
    mod.AssistantMessage = AssistantMessage
    mod.ResultMessage = ResultMessage
    mod.ClaudeAgentOptions = ClaudeAgentOptions
    mod.query = query
    mod._state = state

    monkeypatch.setitem(sys.modules, "claude_agent_sdk", mod)
    return mod


def test_oauth_path_records_zero_cost_row_with_units(monkeypatch, cost_store):
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-live")
    monkeypatch.setenv("CLAUDE_CODE_OAUTH_TOKEN", "oauth-tok")
    # Force the API path into cool-off so OAuth is taken.
    llm.mark_api_key_unusable()
    assert llm._api_key_in_cool_off() is True

    mod = _install_fake_sdk(monkeypatch, messages=[])
    mod._state["messages"] = [
        mod.AssistantMessage([mod.TextBlock("from "), mod.TextBlock("subscription")]),
        mod.ResultMessage(
            subtype="success", is_error=False, result="unused",
            # The SDK exposes usage on the ResultMessage as a plain dict.
            usage={
                "input_tokens": 1_000_000,
                "output_tokens": 1_000_000,
                "cache_read_input_tokens": 0,
                "cache_creation_input_tokens": 0,
            },
        ),
    ]

    with cost.cost_context(stage="tailor", job_id="job-9"):
        out = llm.complete(
            system="SYS", prompt="hi",
            model="claude-opus-4-8", max_tokens=128,
        )

    assert out == "from subscription"

    inserts = cost_store.get("inserts", [])
    assert len(inserts) == 1
    row = inserts[0]["payload"]
    assert row["auth_path"] == "oauth"
    assert row["stage"] == "tailor"
    assert row["job_id"] == "job-9"
    # Subscription call — dollars zeroed, token counts still recorded.
    assert row["cost_usd"] == 0
    assert row["units"]["input_tokens"] == 1_000_000
    assert row["units"]["output_tokens"] == 1_000_000


def test_oauth_path_without_usage_records_visible_marker(monkeypatch, cost_store):
    """When the SDK exposes no usage, a zero-unit oauth marker keeps the
    call visible — we never invent token numbers."""
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-live")
    monkeypatch.setenv("CLAUDE_CODE_OAUTH_TOKEN", "oauth-tok")
    llm.mark_api_key_unusable()

    mod = _install_fake_sdk(monkeypatch, messages=[])
    mod._state["messages"] = [
        mod.ResultMessage(subtype="success", is_error=False, result="answer"),
    ]

    with cost.cost_context(stage="tailor"):
        out = llm.complete(
            system="SYS", prompt="hi",
            model="claude-opus-4-8", max_tokens=128,
        )

    assert out == "answer"
    row = cost_store["inserts"][0]["payload"]
    assert row["auth_path"] == "oauth"
    assert row["cost_usd"] == 0
    assert row["units"] == {
        "input_tokens": 0,
        "output_tokens": 0,
        "cache_read": 0,
        "cache_creation": 0,
    }
