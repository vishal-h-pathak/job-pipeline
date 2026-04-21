"""
test_scaffold.py — Smoke tests to keep the scaffold from silently bit-rotting.

These don't hit Supabase, Browserbase, or Anthropic. They just check that the
modules import and the contracts are wired correctly. Real behavioral tests
land with the Greenhouse adapter in Milestone 3.
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

import pytest

# Make package imports work when running pytest from the repo root.
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))


@pytest.fixture(autouse=True)
def _fill_required_env(monkeypatch):
    """config.py raises at import if required env vars are missing. Supply
    placeholders so imports don't explode during scaffold-only tests."""
    required = {
        "SUPABASE_URL": "https://example.supabase.co",
        "SUPABASE_KEY": "anon-test",
        "SUPABASE_SERVICE_ROLE_KEY": "service-test",
        "BROWSERBASE_API_KEY": "bb-test",
        "BROWSERBASE_PROJECT_ID": "bb-proj-test",
        "ANTHROPIC_API_KEY": "sk-test",
    }
    for k, v in required.items():
        monkeypatch.setenv(k, v)
    # Evict cached modules that read env at import time.
    for m in ("config", "db"):
        sys.modules.pop(m, None)
    yield


def test_config_imports():
    import config
    assert config.AUTO_SUBMIT_THRESHOLD == 0.90
    assert config.ATS_CONFIDENCE_MIN["linkedin"] > 1.0  # sentinel: never auto-submit


def test_adapter_contract_exists():
    from adapters.base import Adapter, SubmissionContext, SubmissionResult, FieldFill
    # Protocols have the expected attrs
    assert hasattr(Adapter, "run")
    r = SubmissionResult()
    assert r.confidence == 0.0
    assert r.recommend == "needs_review"
    f = FieldFill(label="First name", value="Vishal", confidence=0.95)
    assert f.kind == "text"


def test_router_import_safe_without_adapters():
    import router
    # No adapters registered yet; router should handle the empty registry
    # by raising LookupError rather than crashing at import.
    with pytest.raises(LookupError):
        router.get_adapter("greenhouse")


def test_confirm_decide_pure():
    from adapters.base import SubmissionResult
    from confirm import decide
    r = SubmissionResult(confidence=0.95, recommend="auto_submit")
    assert decide(r, "greenhouse") == "submit_and_verify"
    r_low = SubmissionResult(confidence=0.50, recommend="auto_submit")
    assert decide(r_low, "greenhouse") == "route_to_review"
    r_li = SubmissionResult(confidence=0.99, recommend="auto_submit")
    assert decide(r_li, "linkedin") == "route_to_review"  # sentinel threshold > 1
    r_err = SubmissionResult(confidence=0.99, recommend="auto_submit", error="boom")
    assert decide(r_err, "greenhouse") == "abort"


def test_browser_session_is_stub():
    import asyncio
    from browser import session
    async def _try():
        async with session.open_session("https://example.com"):
            pass
    with pytest.raises(NotImplementedError):
        asyncio.run(_try())


def test_review_packet_shape():
    from adapters.base import SubmissionResult, FieldFill
    from review.packet import build_packet
    result = SubmissionResult(
        confidence=0.80,
        filled_fields=[FieldFill(label="Email", value="v@example.com", confidence=1.0)],
        adapter_name="greenhouse",
    )
    p = build_packet(
        job={"id": "abc-123"},
        result=result,
        attempt_n=1,
        stagehand_session_id="sess-xxx",
        browserbase_replay_url="https://www.browserbase.com/sessions/xxx",
        reason="needs human eyes",
    )
    assert p["attempt_n"] == 1
    assert p["adapter"] == "greenhouse"
    assert len(p["filled_fields"]) == 1
    assert p["review_url"].endswith("/abc-123")
