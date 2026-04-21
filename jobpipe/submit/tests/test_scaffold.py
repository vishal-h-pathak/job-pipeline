"""
test_scaffold.py — Smoke tests to keep the scaffold from silently bit-rotting.

These don't hit Supabase, Browserbase, or Anthropic. They check that the
modules import, the contracts are wired correctly, and the Greenhouse adapter
drives its Stagehand/Playwright dependencies in the right order.
"""

from __future__ import annotations

import sys
from pathlib import Path
from types import SimpleNamespace

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
    # Threshold is tuneable (bring-up sets it above 1.0 as a safety stop), but
    # it must be a real float in a sane band.
    assert 0.0 <= config.AUTO_SUBMIT_THRESHOLD <= 2.0
    assert isinstance(config.AUTO_SUBMIT_THRESHOLD, float)
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


def test_router_finds_greenhouse():
    """Once adapters.greenhouse is importable, the router should pick it up
    via the @register decorator — no manual registration call needed."""
    import router
    # Clear registry so this test is idempotent.
    router._REGISTRY.clear()
    adapter = router.get_adapter("greenhouse")
    assert adapter.name == "greenhouse"


def test_router_unknown_falls_back_when_generic_exists(monkeypatch):
    """If we register a 'generic' adapter, unknown kinds resolve to it."""
    import router
    from adapters.base import Adapter, SubmissionContext, SubmissionResult

    class _Fake(Adapter):
        ats_kind = "generic"
        async def run(self, ctx: SubmissionContext) -> SubmissionResult:
            return SubmissionResult(adapter_name="generic")

    router._REGISTRY.clear()
    router._REGISTRY["generic"] = _Fake  # type: ignore[index]
    # Also prevent _import_adapters from clobbering by re-populating
    monkeypatch.setattr(router, "_import_adapters", lambda: None)
    assert router.get_adapter("workday").name == "generic"


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


def _stagehand_deps_installed() -> bool:
    try:
        import stagehand  # noqa: F401
        import playwright  # noqa: F401
    except ImportError:
        return False
    return True


@pytest.mark.skipif(
    _stagehand_deps_installed(),
    reason="stagehand+playwright are installed in this env; the 'missing deps' branch "
           "only fires on CI / fresh clones. Run in a venv without them to exercise.",
)
def test_browser_session_reports_missing_deps():
    """Without stagehand + playwright installed, open_session should raise
    a RuntimeError with install instructions — not a silent ImportError."""
    import asyncio
    from browser import session
    async def _try():
        async with session.open_session("https://example.com"):
            pass
    with pytest.raises(RuntimeError, match="stagehand.*and playwright"):
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


# ── Greenhouse adapter: exercise with fakes ──────────────────────────────

class _FakeLocator:
    def __init__(self, count: int = 1):
        self._count = count
        self.first = self
        self.set_input_files_calls: list[str] = []
    async def count(self): return self._count
    async def set_input_files(self, path: str): self.set_input_files_calls.append(path)


class _FakePage:
    def __init__(self, file_inputs_exist: bool = True):
        self._file_inputs_exist = file_inputs_exist
        self.locator_calls: list[str] = []
        self._locator = _FakeLocator(count=1 if file_inputs_exist else 0)
    def locator(self, sel: str):
        self.locator_calls.append(sel)
        return self._locator


class _FakeStagehandSession:
    def __init__(self, survey: dict, question_answers: dict[str, dict] | None = None):
        self._survey = survey
        self._answers = question_answers or {}
        self.act_calls: list[str] = []
        self.extract_calls: list[str] = []
    # We won't use these directly; the adapter calls the sh_* helpers which
    # await sess.observe/act/extract/execute — but we short-circuit by
    # patching sh_act / sh_extract at the adapter-level instead.


def test_greenhouse_adapter_happy_path(monkeypatch, tmp_path):
    """Core + optional fields all present → confidence 0.95, auto_submit."""
    import asyncio
    from adapters import greenhouse as gh_mod
    from adapters.base import SubmissionContext

    survey = {
        "first_name_present": True, "last_name_present": True,
        "email_present": True, "phone_present": True,
        "resume_present": True, "cover_letter_present": True,
        "linkedin_present": True, "website_present": False,
        "custom_questions": [],
    }

    async def fake_extract(sess, instruction, schema, *, page=None):
        # Only survey extract is called in the happy path
        return survey

    async def fake_act(sess, input, *, page=None):
        return {"message": "ok"}

    monkeypatch.setattr(gh_mod, "sh_extract", fake_extract)
    import adapters._common as cmn
    monkeypatch.setattr(cmn, "sh_act", fake_act)

    resume = tmp_path / "resume.pdf"; resume.write_bytes(b"%PDF-1.4")
    cover  = tmp_path / "cover.pdf";  cover.write_bytes(b"%PDF-1.4")

    ctx = SubmissionContext(
        job={
            "id": "j1", "title": "Eng", "ats_kind": "greenhouse",
            "applicant_profile": {
                "first_name": "Vishal", "last_name": "Pathak",
                "email": "v@example.com", "phone": "555-1212",
                "linkedin_url": "https://linkedin.com/in/v",
            },
        },
        resume_pdf_path=resume,
        cover_letter_pdf_path=cover,
        cover_letter_text="Dear team...",
        application_url="https://boards.greenhouse.io/x/jobs/1",
        stagehand_session=_FakeStagehandSession(survey),
        page=_FakePage(),
        attempt_n=1,
    )

    result = asyncio.run(gh_mod.GreenhouseAdapter().run(ctx))
    assert result.recommend == "auto_submit"
    assert result.confidence >= 0.90
    assert any(f.label == "resume" for f in result.filled_fields)
    assert any(f.label == "cover_letter" for f in result.filled_fields)
    # No required-missing skips.
    assert not any(s.reason.startswith("required") for s in result.skipped_fields)


def test_greenhouse_adapter_routes_to_review_on_missing_resume_input(monkeypatch, tmp_path):
    """If the resume file input can't be located, route to review."""
    import asyncio
    from adapters import greenhouse as gh_mod
    from adapters.base import SubmissionContext

    survey = {
        "first_name_present": True, "last_name_present": True,
        "email_present": True, "phone_present": True,
        "resume_present": True, "cover_letter_present": False,
        "linkedin_present": False, "website_present": False,
        "custom_questions": [],
    }

    async def fake_extract(sess, instruction, schema, *, page=None):
        return survey
    async def fake_act(sess, input, *, page=None):
        return {"message": "ok"}

    monkeypatch.setattr(gh_mod, "sh_extract", fake_extract)
    import adapters._common as cmn
    monkeypatch.setattr(cmn, "sh_act", fake_act)

    resume = tmp_path / "r.pdf"; resume.write_bytes(b"%PDF")
    cover  = tmp_path / "c.pdf"; cover.write_bytes(b"%PDF")

    ctx = SubmissionContext(
        job={"id": "j2", "title": "Eng",
             "applicant_profile": {
                 "first_name": "V", "last_name": "P",
                 "email": "v@x", "phone": "1"}},
        resume_pdf_path=resume,
        cover_letter_pdf_path=cover,
        cover_letter_text="",
        application_url="https://example.com",
        stagehand_session=_FakeStagehandSession(survey),
        page=_FakePage(file_inputs_exist=False),  # ← no file input found
        attempt_n=1,
    )

    result = asyncio.run(gh_mod.GreenhouseAdapter().run(ctx))
    assert result.recommend == "needs_review"
    assert any(s.label == "resume" for s in result.skipped_fields)


def test_greenhouse_adapter_required_custom_q_routes_to_review(monkeypatch, tmp_path):
    """A required custom question we can't answer drops confidence to review."""
    import asyncio
    from adapters import greenhouse as gh_mod
    from adapters.base import SubmissionContext

    survey = {
        "first_name_present": True, "last_name_present": True,
        "email_present": True, "phone_present": True,
        "resume_present": True, "cover_letter_present": False,
        "linkedin_present": False, "website_present": False,
        "custom_questions": [
            {"label": "Are you authorized to work in the US?",
             "kind": "radio", "required": True},
        ],
    }
    # Two extract paths now live in different modules: the survey extract is
    # called from greenhouse.py; the custom-question decision extract is
    # called from adapters/_common.py. Patch both with the same dispatcher.
    async def fake_survey(sess, instruction, schema, *, page=None):
        return survey
    async def fake_decision(sess, instruction, schema, *, page=None):
        return {"decision": "skip", "reason": "no mapping"}

    async def fake_act(sess, input, *, page=None):
        return {"message": "ok"}

    monkeypatch.setattr(gh_mod, "sh_extract", fake_survey)
    import adapters._common as cmn
    monkeypatch.setattr(cmn, "sh_extract", fake_decision)
    monkeypatch.setattr(cmn, "sh_act", fake_act)

    resume = tmp_path / "r.pdf"; resume.write_bytes(b"%PDF")
    cover  = tmp_path / "c.pdf"; cover.write_bytes(b"%PDF")

    ctx = SubmissionContext(
        job={"id": "j3", "title": "Eng",
             "applicant_profile": {
                 "first_name": "V", "last_name": "P",
                 "email": "v@x", "phone": "1"}},
        resume_pdf_path=resume,
        cover_letter_pdf_path=cover,
        cover_letter_text="",
        application_url="https://example.com",
        stagehand_session=_FakeStagehandSession(survey),
        page=_FakePage(),
        attempt_n=1,
    )

    result = asyncio.run(gh_mod.GreenhouseAdapter().run(ctx))
    assert result.recommend == "needs_review"
    assert any(s.reason.startswith("required custom question")
               for s in result.skipped_fields)


def test_lever_adapter_full_name_variant(monkeypatch, tmp_path):
    """Lever board with single full-name field + cover letter textarea should
    auto_submit at 0.90+ with all required fields."""
    import asyncio
    from adapters import lever as lv_mod
    from adapters.base import SubmissionContext

    survey = {
        "full_name_present": True,
        "first_name_present": False, "last_name_present": False,
        "email_present": True, "phone_present": True,
        "resume_present": True, "cover_letter_textarea_present": True,
        "linkedin_present": False, "github_present": False,
        "website_present": False, "current_company_present": False,
        "custom_questions": [],
    }

    async def fake_extract(sess, instruction, schema, *, page=None): return survey
    async def fake_act(sess, input, *, page=None): return {"message": "ok"}

    monkeypatch.setattr(lv_mod, "sh_extract", fake_extract)
    # Patch sh_act through the _common module since that's where fill helpers live
    import adapters._common as cmn
    monkeypatch.setattr(cmn, "sh_act", fake_act)

    resume = tmp_path / "r.pdf"; resume.write_bytes(b"%PDF")
    cover  = tmp_path / "c.pdf"; cover.write_bytes(b"%PDF")

    ctx = SubmissionContext(
        job={"id": "lv1", "title": "SWE",
             "applicant_profile": {
                 "full_name": "Vishal Pathak",
                 "email": "v@example.com", "phone": "555"}},
        resume_pdf_path=resume,
        cover_letter_pdf_path=cover,
        cover_letter_text="Dear hiring team, ..." * 20,
        application_url="https://jobs.lever.co/x/123",
        stagehand_session=_FakeStagehandSession(survey),
        page=_FakePage(),
        attempt_n=1,
    )

    result = asyncio.run(lv_mod.LeverAdapter().run(ctx))
    assert result.recommend == "auto_submit"
    assert result.confidence >= 0.90
    assert any(f.label == "full name" for f in result.filled_fields)
    # Textarea fill should have been recorded too
    assert any("cover letter" in f.label for f in result.filled_fields)


def test_ashby_adapter_missing_location_routes_to_review(monkeypatch, tmp_path):
    """Ashby counts location as a core field — missing it should route to review."""
    import asyncio
    from adapters import ashby as ab_mod
    from adapters.base import SubmissionContext

    survey = {
        "full_name_present": False,
        "first_name_present": True, "last_name_present": True,
        "email_present": True, "phone_present": True,
        "location_present": True,  # form has it, but applicant profile has no location
        "linkedin_present": False, "website_present": False,
        "current_company_present": False, "current_title_present": False,
        "resume_present": True, "cover_letter_textarea_present": False,
        "custom_questions": [],
    }

    async def fake_extract(sess, instruction, schema, *, page=None): return survey
    async def fake_act(sess, input, *, page=None): return {"message": "ok"}

    monkeypatch.setattr(ab_mod, "sh_extract", fake_extract)
    import adapters._common as cmn
    monkeypatch.setattr(cmn, "sh_act", fake_act)

    # Short-circuit the page.wait_for_load_state call by giving it a no-op.
    class _PageWithWait(_FakePage):
        async def wait_for_load_state(self, *a, **kw): return None
    page = _PageWithWait()

    resume = tmp_path / "r.pdf"; resume.write_bytes(b"%PDF")
    cover  = tmp_path / "c.pdf"; cover.write_bytes(b"%PDF")

    ctx = SubmissionContext(
        job={"id": "ab1", "title": "SWE",
             "applicant_profile": {
                 "first_name": "V", "last_name": "P",
                 "email": "v@x", "phone": "1"}},  # no location → skip, core_missing
        resume_pdf_path=resume,
        cover_letter_pdf_path=cover,
        cover_letter_text="",
        application_url="https://jobs.ashbyhq.com/x",
        stagehand_session=_FakeStagehandSession(survey),
        page=page,
        attempt_n=1,
    )

    result = asyncio.run(ab_mod.AshbyAdapter().run(ctx))
    assert result.recommend == "needs_review"
    assert any(s.label == "location" for s in result.skipped_fields)


def test_confirm_signals_fire_on_greenhouse_url():
    """Deterministic URL-needle match should short-circuit the LLM judge."""
    import asyncio
    from types import SimpleNamespace
    import confirm
    from adapters.base import SubmissionContext, SubmissionResult

    class _URLPage:
        url = "https://boards.greenhouse.io/x/applications/thank_you"
        is_closed = lambda self: False
        async def content(self): return ""
        async def wait_for_load_state(self, *a, **kw): return None

    async def fake_sh_act(sess, input, *, page=None): return None
    import browser.session as bs
    # Only click path is monkeypatched; signal probe reads page.url directly
    # and should trigger on the greenhouse URL needle without calling LLM.
    import pytest
    monkey = pytest.MonkeyPatch()
    monkey.setattr(confirm, "sh_act", fake_sh_act)
    try:
        page = _URLPage()
        ctx = SubmissionContext(
            job={"id": "jx", "ats_kind": "greenhouse"},
            resume_pdf_path=Path("/tmp/r.pdf"),
            cover_letter_pdf_path=Path("/tmp/c.pdf"),
            cover_letter_text="",
            application_url="https://example.com",
            stagehand_session=SimpleNamespace(),
            page=page,
            attempt_n=1,
        )
        result = SubmissionResult(confidence=0.95, recommend="auto_submit", adapter_name="greenhouse")
        outcome = asyncio.run(confirm.click_submit_and_verify(ctx, result))
        assert outcome.decision == "submit_and_verify"
        assert outcome.evidence["kind"] == "url_redirect"
    finally:
        monkey.undo()
