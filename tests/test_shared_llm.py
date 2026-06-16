"""tests/test_shared_llm.py — jobpipe.shared.llm auth-fallback chain.

Mirrors the portfolio chat-auth.ts contract in Python:

  API credits first → on a billing/credit/auth failure, bench the key
  (15-min cool-off) and fall through to Claude Agent SDK OAuth →
  else RuntimeError.

Transient errors (429 / 5xx) must NOT bench the key — they propagate so
the caller's per-job failure handling catches them.

Fully mocked: no network, no `claude_agent_sdk` import (the OAuth path is
patched out), no real Anthropic client.
"""

from __future__ import annotations

import sys
import types

import pytest

from jobpipe.shared import llm


# ── Fakes ──────────────────────────────────────────────────────────────────

class _FakeBlock:
    def __init__(self, text: str):
        self.text = text


class _FakeResp:
    def __init__(self, text: str):
        self.content = [_FakeBlock(text)]


class _StatusError(Exception):
    """Stand-in for an anthropic.APIStatusError — only the attributes
    is_api_key_unusable_error reads (`status_code`, `message`)."""

    def __init__(self, status_code: int, message: str = ""):
        super().__init__(message)
        self.status_code = status_code
        self.message = message


class _RecordingClient:
    """Returns a canned body and records every messages.create() kwargs."""

    def __init__(self, text: str):
        self.sent: list[dict] = []
        outer = self

        class _Messages:
            def create(self, **kwargs):
                outer.sent.append(kwargs)
                return _FakeResp(text)

        self.messages = _Messages()


class _RaisingClient:
    """Raises `exc` on every create(); counts how many times it was built."""

    def __init__(self, exc: Exception, counter: list):
        self._exc = exc
        outer = self

        class _Messages:
            def create(self, **kwargs):
                counter.append(kwargs)
                raise outer._exc

        self.messages = _Messages()


@pytest.fixture(autouse=True)
def _reset_cool_off(monkeypatch):
    """Each test starts with a fresh (un-benched) key."""
    monkeypatch.setattr(llm, "_api_key_cool_off_until", 0.0)


# ── is_api_key_unusable_error truth table (mirrors chat-auth.ts) ────────────

@pytest.mark.parametrize(
    "status,message,expected",
    [
        (401, "anything", True),                       # invalid key
        (400, "Your credit balance is too low", True),  # billing
        (402, "payment required", True),
        (403, "your plan does not include this", True),
        (400, "missing required field 'model'", False),  # 400 but not billing
        (403, "forbidden resource", False),              # 403 but not billing
        (429, "rate limit exceeded", False),             # transient
        (500, "internal server error", False),           # transient
        (529, "overloaded", False),                      # transient
        (None, "no status at all", False),               # network/other
    ],
)
def test_is_api_key_unusable_error_truth_table(status, message, expected):
    if status is None:
        exc = Exception(message)
    else:
        exc = _StatusError(status, message)
    assert llm.is_api_key_unusable_error(exc) is expected


# ── Chain behavior ──────────────────────────────────────────────────────────

def test_api_path_returns_joined_text_without_touching_oauth(monkeypatch):
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-live")
    client = _RecordingClient("hello from credits")
    monkeypatch.setattr(llm, "_anthropic_client", lambda *a, **k: client)

    def _no_oauth(**kwargs):
        raise AssertionError("OAuth path must not run while credits work")

    monkeypatch.setattr(llm, "_oauth_complete", _no_oauth)

    out = llm.complete(
        system=[{"type": "text", "text": "SYS"}],
        prompt="hi",
        model="claude-sonnet-4-20250514",
        max_tokens=100,
    )
    assert out == "hello from credits"
    assert client.sent[0]["model"] == "claude-sonnet-4-20250514"
    assert client.sent[0]["max_tokens"] == 100
    assert client.sent[0]["system"] == [{"type": "text", "text": "SYS"}]
    assert client.sent[0]["messages"] == [{"role": "user", "content": "hi"}]


def test_billing_error_benches_key_and_falls_through_to_oauth(monkeypatch):
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-dead")
    monkeypatch.setenv("CLAUDE_CODE_OAUTH_TOKEN", "oauth-tok")

    builds: list = []
    billing = _StatusError(400, "Your credit balance is too low to proceed")
    monkeypatch.setattr(
        llm, "_anthropic_client",
        lambda *a, **k: _RaisingClient(billing, builds),
    )

    oauth_calls: list = []

    def _fake_oauth(*, system_text, prompt, model, token):
        oauth_calls.append({"system_text": system_text, "prompt": prompt,
                            "model": model, "token": token})
        return "from subscription"

    monkeypatch.setattr(llm, "_oauth_complete", _fake_oauth)

    out = llm.complete(
        system=[{"type": "text", "text": "SYS"}],
        prompt="hi",
        model="claude-sonnet-4-20250514",
        max_tokens=100,
    )

    # Fell through to OAuth and returned its text.
    assert out == "from subscription"
    assert len(builds) == 1, "API path attempted exactly once"
    assert len(oauth_calls) == 1
    # System blocks flattened to a plain string for the OAuth path.
    assert oauth_calls[0]["system_text"] == "SYS"
    assert oauth_calls[0]["token"] == "oauth-tok"

    # Key is now benched: a SECOND call skips the API entirely.
    out2 = llm.complete(
        system="SYS", prompt="again",
        model="claude-sonnet-4-20250514", max_tokens=100,
    )
    assert out2 == "from subscription"
    assert len(builds) == 1, "API client must not be rebuilt while benched"
    assert len(oauth_calls) == 2


def test_rate_limit_error_propagates_and_does_not_bench(monkeypatch):
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-live")
    monkeypatch.setenv("CLAUDE_CODE_OAUTH_TOKEN", "oauth-tok")

    builds: list = []
    rate_limited = _StatusError(429, "rate limit exceeded")
    monkeypatch.setattr(
        llm, "_anthropic_client",
        lambda *a, **k: _RaisingClient(rate_limited, builds),
    )

    def _no_oauth(**kwargs):
        raise AssertionError("transient error must not reach OAuth")

    monkeypatch.setattr(llm, "_oauth_complete", _no_oauth)

    with pytest.raises(_StatusError):
        llm.complete(system="SYS", prompt="hi",
                     model="claude-sonnet-4-20250514", max_tokens=100)

    # Key not benched → a healthy key gets retried next time.
    assert llm._api_key_in_cool_off() is False
    assert len(builds) == 1


def test_no_auth_configured_raises_runtime_error(monkeypatch):
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    monkeypatch.delenv("CLAUDE_CODE_OAUTH_TOKEN", raising=False)

    with pytest.raises(RuntimeError, match="no usable Anthropic auth"):
        llm.complete(system="SYS", prompt="hi",
                     model="claude-sonnet-4-20250514", max_tokens=100)


def test_missing_key_uses_oauth_directly(monkeypatch):
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    monkeypatch.setenv("CLAUDE_CODE_OAUTH_TOKEN", "oauth-tok")

    def _no_client(*a, **k):
        raise AssertionError("must not build an API client without a key")

    monkeypatch.setattr(llm, "_anthropic_client", _no_client)
    monkeypatch.setattr(
        llm, "_oauth_complete",
        lambda **kwargs: "subscription only",
    )

    out = llm.complete(system="SYS", prompt="hi",
                       model="claude-sonnet-4-20250514", max_tokens=100)
    assert out == "subscription only"


# ── OAuth subprocess env ────────────────────────────────────────────────────

def test_subprocess_env_blanks_api_key_and_sets_oauth_token(monkeypatch):
    """The Agent SDK merges `env` into the inherited environment, and the
    CLI ranks ANTHROPIC_API_KEY above CLAUDE_CODE_OAUTH_TOKEN. A
    billing-blocked key must therefore be explicitly BLANKED (not merely
    omitted), or the inherited value would shadow the OAuth token."""
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-dead")
    monkeypatch.setenv("SOME_OTHER_VAR", "keep-me")

    env = llm._subprocess_env("oauth-tok")

    # Present and empty — overrides the inherited billing-blocked value.
    assert env["ANTHROPIC_API_KEY"] == ""
    assert env["CLAUDE_CODE_OAUTH_TOKEN"] == "oauth-tok"
    # Other inherited vars are preserved.
    assert env["SOME_OTHER_VAR"] == "keep-me"


# ── flatten_system ──────────────────────────────────────────────────────────

def test_flatten_system_passes_through_a_string():
    assert llm.flatten_system("already a string") == "already a string"


def test_flatten_system_joins_cached_blocks():
    blocks = [
        {"type": "text", "text": "rules"},
        {"type": "text", "text": "profile", "cache_control": {"type": "ephemeral"}},
    ]
    assert llm.flatten_system(blocks) == "rules\n\nprofile"


# ── OAuth result handling (real _oauth_complete against a fake SDK) ──────────
#
# `_oauth_complete` lazy-imports `claude_agent_sdk`, so we inject a fake
# module into sys.modules and exercise the REAL _oauth_complete + _run_sync.
# No SDK install, no network — the fake `query` is an async generator that
# yields fake AssistantMessage / ResultMessage instances built from the same
# classes the module exposes (so the production isinstance checks hold).

def _install_fake_sdk(monkeypatch):
    mod = types.ModuleType("claude_agent_sdk")
    state: dict = {"messages": [], "options": None}

    class TextBlock:
        def __init__(self, text):
            self.text = text

    class AssistantMessage:
        def __init__(self, content):
            self.content = content

    class ResultMessage:
        def __init__(self, subtype, is_error=False, result=None):
            self.subtype = subtype
            self.is_error = is_error
            self.result = result

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


def _call_oauth():
    return llm._oauth_complete(
        system_text="SYS", prompt="hi",
        model="claude-opus-4-20250514", token="oauth-tok",
    )


def test_oauth_success_returns_accumulated_text(monkeypatch):
    """Regression for the 'success' misread: a stream ending in a
    ResultMessage(subtype='success') with assistant text returns that
    text — success is the happy path, not an error."""
    mod = _install_fake_sdk(monkeypatch)
    mod._state["messages"] = [
        mod.AssistantMessage([mod.TextBlock("tailored "), mod.TextBlock("resume")]),
        mod.ResultMessage(subtype="success", is_error=False, result="unused"),
    ]
    assert _call_oauth() == "tailored resume"


def test_oauth_success_falls_back_to_result_field(monkeypatch):
    """When the final text lands in ResultMessage.result rather than a
    TextBlock, success still returns it."""
    mod = _install_fake_sdk(monkeypatch)
    mod._state["messages"] = [
        mod.ResultMessage(subtype="success", is_error=False, result="final answer"),
    ]
    assert _call_oauth() == "final answer"


def test_oauth_max_turns_raises_clear_error(monkeypatch):
    """A stream ending in error_max_turns raises a clear, distinct error
    that mentions max turns — never silently swallowed or returned."""
    mod = _install_fake_sdk(monkeypatch)
    mod._state["messages"] = [
        mod.ResultMessage(
            subtype="error_max_turns", is_error=True,
            result="Reached maximum number of turns (4)",
        ),
    ]
    with pytest.raises(llm.OAuthCompletionError) as ei:
        _call_oauth()
    msg = str(ei.value).lower()
    assert "max" in msg and "turn" in msg
    assert ei.value.subtype == "error_max_turns"


def test_oauth_error_subtype_includes_partial_text(monkeypatch):
    """On a genuine error subtype, any partial assistant text is carried
    on the raised error for debuggability."""
    mod = _install_fake_sdk(monkeypatch)
    mod._state["messages"] = [
        mod.AssistantMessage([mod.TextBlock("half a resume")]),
        mod.ResultMessage(subtype="error_during_execution", is_error=True),
    ]
    with pytest.raises(llm.OAuthCompletionError) as ei:
        _call_oauth()
    assert ei.value.partial_text == "half a resume"
    assert "half a resume" in str(ei.value)


def test_oauth_uses_max_turns_headroom(monkeypatch):
    """The turns cap is bumped above 1 so a legitimate single-answer
    completion the harness accounts as >1 turn doesn't trip the cap."""
    mod = _install_fake_sdk(monkeypatch)
    mod._state["messages"] = [
        mod.ResultMessage(subtype="success", is_error=False, result="ok"),
    ]
    _call_oauth()
    opts = mod._state["options"]
    assert opts["max_turns"] == llm._OAUTH_MAX_TURNS
    assert opts["max_turns"] > 1
    # Fallback invariants left unchanged.
    assert opts["allowed_tools"] == []
    assert opts["setting_sources"] == []
    assert opts["env"]["ANTHROPIC_API_KEY"] == ""
    assert opts["env"]["CLAUDE_CODE_OAUTH_TOKEN"] == "oauth-tok"
