"""jobpipe.shared.llm — Anthropic completion with a credits-first,
subscription-OAuth fallback.

Ported from the portfolio repo's chat-auth.ts / chat-oauth.ts, which
solved the same problem for its chat route. Resolution chain, evaluated
per call:

  1. API path (preferred). ANTHROPIC_API_KEY set AND not in cool-off →
     Messages API with prompt caching preserved (``system`` passed through
     verbatim, including the cached-blocks list).
  2. OAuth path (fallback). The API key is absent, or was just benched by
     a billing/credit/auth failure, AND CLAUDE_CODE_OAUTH_TOKEN is set →
     Claude Agent SDK under subscription auth. Prompt caching does not
     apply here (that's fine — it's the fallback).
  3. Neither → RuntimeError.

When an API call fails with a billing/credit error or an invalid key,
``mark_api_key_unusable()`` benches the key for 15 minutes so subsequent
calls fall straight through to OAuth instead of burning a doomed attempt
per job. Transient errors (429, 5xx, network) are re-raised WITHOUT
benching — a healthy key must not be sidelined by a blip; the caller's
per-job failure handling deals with them.

The cool-off lives in module state: process-wide, reset when the process
restarts. Worst case after a restart is one failed call before the
process re-learns the key is dead.

Two places make real model calls — keep them isolated and patchable:
``_anthropic_client`` (Messages API client factory) and
``_oauth_complete`` (Agent SDK adapter). Tests monkeypatch these.
"""

from __future__ import annotations

import asyncio
import logging
import os
import re
import time
from typing import Any, Union

import anthropic

logger = logging.getLogger("jobpipe.shared.llm")

# Cool-off (one attempt + 15 min, no retries), matching chat-auth.ts.
_COOL_OFF_SECONDS = 15 * 60
_api_key_cool_off_until: float = 0.0

# Billing/credit/plan failure signatures on a 400/402/403 response.
_UNUSABLE_MSG = re.compile(r"billing|credit|balance|purchase|payment|plan", re.I)

# System content the Messages API accepts: a plain string, or the
# cached-blocks list (list of ``{"type": "text", "text": ...}`` dicts).
SystemContent = Union[str, list]


# ── Auth-error classification ───────────────────────────────────────────────

def is_api_key_unusable_error(exc: BaseException) -> bool:
    """True for errors that mean the key won't work until a human
    intervenes: an invalid-key auth failure (401) or a billing/credit
    failure (400/402/403 whose message matches the billing signatures).

    Transient errors (429, 5xx, network — no/other status) return False so
    a healthy key isn't benched by a blip. Mirrors chat-auth.ts exactly.
    """
    status = getattr(exc, "status_code", None)
    if status == 401:
        return True
    if status in (400, 402, 403):
        msg = getattr(exc, "message", None) or str(exc)
        return bool(_UNUSABLE_MSG.search(msg))
    return False


def mark_api_key_unusable() -> None:
    """Bench ANTHROPIC_API_KEY for the next 15 minutes."""
    global _api_key_cool_off_until
    _api_key_cool_off_until = time.monotonic() + _COOL_OFF_SECONDS


def _api_key_in_cool_off() -> bool:
    return time.monotonic() < _api_key_cool_off_until


# ── System-prompt flattening (OAuth path has no prompt caching) ─────────────

def flatten_system(system: SystemContent) -> str:
    """Join the cached-blocks ``system`` into one plain string for the
    OAuth path. A string passes through untouched."""
    if isinstance(system, str):
        return system
    parts: list[str] = []
    for block in system or []:
        if isinstance(block, dict):
            parts.append(block.get("text", ""))
        else:
            parts.append(getattr(block, "text", "") or str(block))
    return "\n\n".join(p for p in parts if p)


# ── API path (preferred) ────────────────────────────────────────────────────

def _anthropic_client(api_key: str) -> anthropic.Anthropic:
    """Build a Messages API client. Isolated so tests can patch it."""
    return anthropic.Anthropic(api_key=api_key)


def _join_text(resp: Any) -> str:
    return "".join(b.text for b in resp.content if hasattr(b, "text"))


# ── OAuth path (fallback) ───────────────────────────────────────────────────

# The Agent SDK takes model aliases or full IDs; the dated Messages-API
# string (e.g. "claude-sonnet-4-20250514") can be rejected, so map the
# known families to their alias for the OAuth path only.
def _oauth_model(model: str) -> str:
    if model.startswith("claude-opus"):
        return "opus"
    if model.startswith("claude-sonnet"):
        return "sonnet"
    if model.startswith("claude-haiku"):
        return "haiku"
    return model


def _subprocess_env(token: str) -> dict:
    """Build the Agent SDK subprocess env: inherit os.environ, BLANK
    ANTHROPIC_API_KEY, and set CLAUDE_CODE_OAUTH_TOKEN.

    Per the Agent SDK docs, ``env`` is MERGED into the inherited
    environment (not replaced), and the CLI ranks ANTHROPIC_API_KEY ABOVE
    CLAUDE_CODE_OAUTH_TOKEN in its auth precedence. So merely dropping the
    key from this dict isn't enough — the inherited (billing-blocked) value
    would still reach the subprocess and shadow the OAuth token, reproducing
    the original "credit balance too low" failure. Setting it to "" forces
    an override of the inherited value; an empty key reads as absent, so the
    CLI falls through to the subscription token. Mirrors chat-oauth.ts'
    intent under Python's merge semantics."""
    env = {k: v for k, v in os.environ.items() if k != "ANTHROPIC_API_KEY"}
    env["ANTHROPIC_API_KEY"] = ""
    env["CLAUDE_CODE_OAUTH_TOKEN"] = token
    return env


def _run_sync(coro):
    """Drive an async coroutine to completion from sync code. The tailor
    batch runs synchronously (no ambient loop); fall back to a private
    loop if one is already running."""
    try:
        return asyncio.run(coro)
    except RuntimeError:
        loop = asyncio.new_event_loop()
        try:
            return loop.run_until_complete(coro)
        finally:
            loop.close()


def _oauth_complete(*, system_text: str, prompt: str, model: str, token: str) -> str:
    """One-shot completion through the Claude Agent SDK under subscription
    OAuth. Lazy-imports the SDK so this module imports cleanly where the
    package (and its bundled Claude Code CLI) isn't installed."""
    from claude_agent_sdk import (  # noqa: PLC0415 — lazy, optional dep
        AssistantMessage,
        ClaudeAgentOptions,
        ResultMessage,
        TextBlock,
        query,
    )

    options = ClaudeAgentOptions(
        system_prompt=system_text,
        model=_oauth_model(model),
        max_turns=1,
        allowed_tools=[],
        setting_sources=[],
        env=_subprocess_env(token),
    )

    async def _run() -> str:
        parts: list[str] = []
        result_text = ""
        async for msg in query(prompt=prompt, options=options):
            if isinstance(msg, AssistantMessage):
                for block in msg.content:
                    if isinstance(block, TextBlock):
                        parts.append(block.text)
            elif isinstance(msg, ResultMessage):
                result_text = getattr(msg, "result", "") or ""
        return "".join(parts) or result_text

    return _run_sync(_run())


# ── Public entry point ──────────────────────────────────────────────────────

def complete(*, system: SystemContent, prompt: str, model: str, max_tokens: int) -> str:
    """Return assistant text. Try the Messages API (pay-as-you-go credits)
    first; on a billing/credit/auth failure, bench the key and fall back to
    the Claude Agent SDK under subscription OAuth.

    ``system`` may be the cached-blocks list (Messages API, prompt caching
    preserved) or a plain string. Raises on transient API errors (the
    caller's per-job handling catches them) and when no auth is usable.
    """
    api_key = (os.environ.get("ANTHROPIC_API_KEY") or "").strip()

    if api_key and not _api_key_in_cool_off():
        try:
            resp = _anthropic_client(api_key).messages.create(
                model=model,
                max_tokens=max_tokens,
                system=system,
                messages=[{"role": "user", "content": prompt}],
            )
            return _join_text(resp)
        except Exception as exc:  # noqa: BLE001 — classify, then re-raise or fall through
            if not is_api_key_unusable_error(exc):
                raise
            mark_api_key_unusable()
            logger.warning(
                "ANTHROPIC_API_KEY unusable (%s); benching for %d min and "
                "falling back to subscription OAuth",
                exc, _COOL_OFF_SECONDS // 60,
            )

    token = (os.environ.get("CLAUDE_CODE_OAUTH_TOKEN") or "").strip()
    if token:
        return _oauth_complete(
            system_text=flatten_system(system),
            prompt=prompt,
            model=model,
            token=token,
        )

    raise RuntimeError(
        "no usable Anthropic auth: API key absent/benched and "
        "CLAUDE_CODE_OAUTH_TOKEN unset"
    )
