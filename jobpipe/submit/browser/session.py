"""
browser/session.py — Browserbase + Stagehand session wrapper.

One SubmissionSession per submit attempt. Always records. Hard-caps runtime
via SESSION_BUDGET_SECONDS. Exposes the Stagehand Page so adapters can call
act() / extract() / observe() directly — we don't re-wrap the API.

NOTE: stub. Milestone 3 wires the actual Stagehand + Browserbase client calls.
Left as NotImplementedError so an accidental run fails loudly, not silently.
"""

from __future__ import annotations

import logging
from contextlib import asynccontextmanager
from dataclasses import dataclass
from typing import Any, AsyncIterator

from config import (
    BROWSERBASE_API_KEY,
    BROWSERBASE_PROJECT_ID,
    HEADLESS,
    SESSION_BUDGET_SECONDS,
)

logger = logging.getLogger("submitter.browser")


@dataclass
class SessionHandle:
    """Reference + metadata for an active Browserbase session."""

    stagehand_session_id: str
    browserbase_replay_url: str
    page: Any                             # Stagehand Page object
    started_at_epoch: float


@asynccontextmanager
async def open_session(url: str) -> AsyncIterator[SessionHandle]:
    """
    Open a Browserbase session, hand back a Stagehand Page positioned at url,
    guarantee cleanup on exit. Enforce SESSION_BUDGET_SECONDS elsewhere via
    a race with asyncio.wait_for at the caller.

    Milestone 3 fills this in using stagehand-py's Browserbase integration.
    """
    _ = (BROWSERBASE_API_KEY, BROWSERBASE_PROJECT_ID, HEADLESS, SESSION_BUDGET_SECONDS, url)
    raise NotImplementedError(
        "browser.session.open_session is a scaffold stub. "
        "Wire up Stagehand + Browserbase in Milestone 3."
    )
    # Unreachable but required so @asynccontextmanager treats this function
    # as an async generator rather than a plain coroutine.
    yield  # type: ignore[unreachable]
