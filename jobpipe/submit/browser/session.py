"""
browser/session.py — Browserbase + Stagehand session wrapper.

╔══════════════════════════════════════════════════════════════════════╗
║  LEGACY (Path B). Drives the retired Browserbase + Stagehand path.   ║
║  Path A (``tailor/pipeline.py::process_prefill_requested_jobs``)     ║
║  uses local Playwright directly — no remote browser. Kept ONLY       ║
║  because this is the sole Browserbase integration in the repo and    ║
║  is worth preserving as reference for any future remote-browser      ║
║  fallback. No live code path imports it. Do not extend.              ║
╚══════════════════════════════════════════════════════════════════════╝

One SubmissionSession per submit attempt. Always records. Hard-caps runtime
via SESSION_BUDGET_SECONDS (enforced by the caller with asyncio.wait_for).

Shape of the stack:

    AsyncStagehand  ─ starts a Stagehand API client
       │
       └── sessions.start(browser={"type":"browserbase"})
              │
              ├── session.id            (Stagehand session id; used for act/extract/observe)
              ├── session.data.cdp_url  (attach Playwright to the same Chromium)
              └── session.data.browserbase_session_id  (drives the replay URL)

The adapter gets BOTH a Stagehand session (for act / extract / observe /
execute) AND a Playwright async Page (for file uploads and frame-scoped
ops). The Stagehand calls can optionally be scoped to the Playwright page
via the `page=` kwarg so Stagehand targets the right frame.
"""

from __future__ import annotations

import asyncio
import logging
import time
from contextlib import asynccontextmanager
from dataclasses import dataclass
from typing import Any, AsyncIterator

from jobpipe.config import SESSION_BUDGET_SECONDS
from jobpipe.submit.config import (
    ANTHROPIC_API_KEY,
    BROWSERBASE_API_KEY,
    BROWSERBASE_PROJECT_ID,
)

logger = logging.getLogger("submitter.browser")

# Stagehand's streaming events on act/extract/observe/execute return a result
# inside an event whose status == "finished". Non-streaming calls exist too
# but the v3 examples lean on streaming; we wrap it once here.
_STAGEHAND_STREAM_KWARGS = {"stream_response": True, "x_stream_response": "true"}

# Per-call hard timeout for Stagehand observe/act/extract. A single stalled
# LLM response must not be able to burn the whole session budget — the phase
# budget + outer SESSION_BUDGET_SECONDS are the other two layers of defense.
# 45s is ~2× the slowest observed call in the Anthropic smoke (37s end-to-end
# with six acts, so ~20s per act worst case); pathological calls trip this.
SH_CALL_TIMEOUT_SECONDS = 45


@dataclass
class SessionHandle:
    """Reference + metadata for an active Browserbase-backed Stagehand session."""

    stagehand_session_id: str
    browserbase_session_id: str
    browserbase_replay_url: str
    stagehand_session: Any                 # Stagehand session object (act/extract/observe/execute live here)
    page: Any                              # Playwright async Page attached over CDP
    started_at_epoch: float


def _replay_url(browserbase_session_id: str) -> str:
    return f"https://www.browserbase.com/sessions/{browserbase_session_id}"


async def _stream_to_result(stream, label: str) -> Any:
    """Consume a Stagehand event stream and return the final `result` payload.

    Mirrors the helper in stagehand-python's examples: logs `log` events for
    debugging, extracts the `finished` event's result, and raises on `error`.
    """
    result: Any = None
    async for event in stream:
        if getattr(event, "type", None) == "log":
            logger.debug("[%s][log] %s", label, event.data.message)
            continue
        status = event.data.status
        logger.debug("[%s][status] %s", label, status)
        if status == "finished":
            result = event.data.result
        elif status == "error":
            msg = event.data.error or "unknown error"
            raise RuntimeError(f"stagehand.{label} error: {msg}")
    return result


@asynccontextmanager
async def open_session(url: str) -> AsyncIterator[SessionHandle]:
    """
    Open a Browserbase-backed Stagehand session, navigate to `url`, hand
    back a SessionHandle, and guarantee cleanup on exit.

    Imports are lazy so `config.py`-level tests don't need stagehand/playwright
    installed.
    """
    # Lazy imports: keep `from browser import session` cheap for unit tests.
    try:
        from stagehand import AsyncStagehand
        from playwright.async_api import async_playwright
    except ImportError as exc:  # pragma: no cover - surfaced at first real run
        raise RuntimeError(
            f"browser.session needs stagehand-py and playwright installed: {exc}. "
            "Run: pip install -r requirements.txt && playwright install chromium"
        ) from exc

    started = time.time()
    async with AsyncStagehand(
        browserbase_api_key=BROWSERBASE_API_KEY,
        browserbase_project_id=BROWSERBASE_PROJECT_ID,
        model_api_key=ANTHROPIC_API_KEY,
    ) as client:
        session = await client.sessions.start(
            model_name="anthropic/claude-sonnet-4-6",
            browser={"type": "browserbase"},
        )
        stagehand_session_id = session.id
        browserbase_session_id = getattr(session.data, "browserbase_session_id", None) \
            or getattr(session.data, "session_id", None) \
            or stagehand_session_id
        cdp_url = session.data.cdp_url
        if not cdp_url:
            raise RuntimeError("Stagehand did not return a cdp_url; cannot attach Playwright.")

        logger.info(
            "stagehand session %s started (bb=%s, budget=%ds)",
            stagehand_session_id, browserbase_session_id, SESSION_BUDGET_SECONDS,
        )

        async with async_playwright() as p:
            browser = await p.chromium.connect_over_cdp(cdp_url)
            try:
                context = browser.contexts[0] if browser.contexts else await browser.new_context()
                page = context.pages[0] if context.pages else await context.new_page()
                await page.goto(url, wait_until="domcontentloaded")

                handle = SessionHandle(
                    stagehand_session_id=stagehand_session_id,
                    browserbase_session_id=browserbase_session_id,
                    browserbase_replay_url=_replay_url(browserbase_session_id),
                    stagehand_session=session,
                    page=page,
                    started_at_epoch=started,
                )
                try:
                    yield handle
                finally:
                    # Best-effort pre-close screenshot for forensics; the session
                    # replay URL is the primary evidence, so a failure here is
                    # non-fatal.
                    try:
                        if not page.is_closed():
                            await page.screenshot(full_page=False)  # cached by Browserbase recording
                    except Exception:
                        pass
            finally:
                try:
                    await browser.close()
                except Exception:
                    logger.warning("playwright browser.close() failed", exc_info=True)
        try:
            await session.end()
        except Exception:
            logger.warning("stagehand session.end() failed", exc_info=True)
        logger.info(
            "stagehand session %s closed after %.1fs",
            stagehand_session_id, time.time() - started,
        )


# ── Stream-aware convenience wrappers ─────────────────────────────────────
#
# Adapters can call these directly; each one returns the final result payload
# from Stagehand's event stream, hiding the streaming boilerplate.
# Passing `page=` is optional but recommended — it lets Stagehand scope to the
# right frame on multi-frame forms (Greenhouse embeds via iframe sometimes).


async def _call_with_timeout(coro_factory, label: str, timeout: float):
    """Run a Stagehand call with a hard per-call wall-clock cap.

    A stalled observe/act/extract must not be able to eat the whole session
    budget. asyncio.wait_for cancels the awaitable on timeout, which lets
    the caller record a FieldSkipped and keep going.
    """
    try:
        return await asyncio.wait_for(coro_factory(), timeout=timeout)
    except asyncio.TimeoutError as exc:
        logger.warning("stagehand.%s exceeded %.0fs per-call timeout", label, timeout)
        raise TimeoutError(f"stagehand.{label} timed out after {timeout:.0f}s") from exc


async def sh_observe(
    sess: Any, instruction: str, *, page: Any | None = None,
    timeout: float = SH_CALL_TIMEOUT_SECONDS,
) -> list[dict]:
    async def _run():
        stream = await sess.observe(instruction=instruction, page=page, **_STAGEHAND_STREAM_KWARGS)
        return await _stream_to_result(stream, "observe")
    result = await _call_with_timeout(_run, "observe", timeout)
    return result if isinstance(result, list) else []


async def sh_act(
    sess: Any, input: Any, *, page: Any | None = None,
    timeout: float = SH_CALL_TIMEOUT_SECONDS,
) -> dict | str | None:
    async def _run():
        stream = await sess.act(input=input, page=page, **_STAGEHAND_STREAM_KWARGS)
        return await _stream_to_result(stream, "act")
    return await _call_with_timeout(_run, "act", timeout)


async def sh_extract(
    sess: Any,
    instruction: str,
    schema: dict,
    *,
    page: Any | None = None,
    timeout: float = SH_CALL_TIMEOUT_SECONDS,
) -> Any:
    async def _run():
        stream = await sess.extract(
            instruction=instruction,
            schema=schema,
            page=page,
            **_STAGEHAND_STREAM_KWARGS,
        )
        return await _stream_to_result(stream, "extract")
    return await _call_with_timeout(_run, "extract", timeout)


async def sh_execute(
    sess: Any,
    instruction: str,
    *,
    max_steps: int = 15,
    model_name: str = "anthropic/claude-sonnet-4-6",
    timeout: float = 240.0,
) -> dict | None:
    stream = await sess.execute(
        execute_options={"instruction": instruction, "max_steps": max_steps},
        agent_config={
            "model": {"model_name": model_name, "api_key": ANTHROPIC_API_KEY},
            "cua": False,
        },
        timeout=timeout,
        **_STAGEHAND_STREAM_KWARGS,
    )
    return await _stream_to_result(stream, "execute")
