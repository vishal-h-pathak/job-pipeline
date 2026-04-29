"""
test_submit_scaffold.py — Smoke tests to keep the submit scaffold from silently
bit-rotting.

PR-10 moved this file from `jobpipe/submit/tests/test_scaffold.py` so it
gets collected by `pyproject.toml::testpaths = ["tests"]` like every
other unit test. The local `_FakePage`/`_FakeLocator`/`_FakeStagehandSession`
helpers it used to define inline are now factory fixtures in
`tests/conftest.py` (`fake_page`, `fake_locator`, `fake_browser`).

These don't hit Supabase, Browserbase, or Anthropic. They check that the
submit modules import, the contracts are wired correctly, and the
deterministic adapters drive their Stagehand/Playwright dependencies in
the right order.
"""

from __future__ import annotations

import sys
from pathlib import Path
from types import SimpleNamespace

import pytest


@pytest.fixture(autouse=True)
def _fill_required_env(monkeypatch):
    """jobpipe/submit/config.py raises at import if required env vars are
    missing (require_env block, fail-loud). Supply placeholders so imports
    don't explode during scaffold-only tests.

    Importing ``jobpipe.submit.runner_legacy`` triggers the submit
    subtree's sys.path bootstrap (the local-Playwright consolidation
    renamed ``runner.py`` to ``runner_legacy.py``), so the bare
    ``import router`` / ``from adapters.base import ...`` imports
    further down each test resolve cleanly even though this file
    now sits under ``tests/legacy/`` and is excluded from the default
    pytest invocation.
    """
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
    # Evict cached modules that read env at import time so each test gets
    # a fresh import. Both the bare ``config``/``db`` (resolved via the
    # submit bootstrap) and the canonical ``jobpipe.config``/``jobpipe.db``.
    for m in ("config", "db", "jobpipe.config", "jobpipe.db",
              "jobpipe.submit.config"):
        sys.modules.pop(m, None)
    # Trigger the submit subtree's sys.path bootstrap so bare imports work
    # from this directory. Local-Playwright consolidation renamed runner.py
    # to runner_legacy.py — both files contain the identical bootstrap.
    import jobpipe.submit.runner_legacy  # noqa: F401
    yield


def test_config_imports():
    # PR-9: AUTO_SUBMIT_THRESHOLD and ATS_CONFIDENCE_MIN live on canonical
    # jobpipe.config (the per-subtree submit/config.py shim re-exports were
    # removed; submit/config.py keeps only the require_env-loaded secrets +
    # the CLAUDE_MODEL alias).
    import jobpipe.config as config
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
    from review_packet import build_packet
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


def test_greenhouse_adapter_happy_path(monkeypatch, tmp_path, fake_page, fake_browser):
    """All core fields present → confidence 0.95, auto_submit.

    Mandatory-only policy: the survey no longer reports LinkedIn/website,
    and even if it did the adapter wouldn't fill them.
    """
    import asyncio
    from adapters.deterministic import greenhouse as gh_mod
    from adapters.base import SubmissionContext

    survey = {
        "first_name_present": True, "last_name_present": True,
        "email_present": True, "phone_present": True,
        "resume_present": True, "cover_letter_present": True,
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
                # LinkedIn intentionally present in profile but never fillable.
                "linkedin_url": "https://linkedin.com/in/v",
            },
        },
        resume_pdf_path=resume,
        cover_letter_pdf_path=cover,
        cover_letter_text="Dear team...",
        application_url="https://boards.greenhouse.io/x/jobs/1",
        stagehand_session=fake_browser(survey=survey),
        page=fake_page(),
        attempt_n=1,
    )

    result = asyncio.run(gh_mod.GreenhouseAdapter().run(ctx))
    assert result.recommend == "auto_submit"
    assert result.confidence >= 0.90
    assert any(f.label == "resume" for f in result.filled_fields)
    assert any(f.label == "cover_letter" for f in result.filled_fields)
    # Policy: no LinkedIn/website/etc. should ever land in filled_fields.
    assert not any(f.label in ("linkedin", "website") for f in result.filled_fields)
    # No required-missing skips.
    assert not any(s.reason.startswith("required") for s in result.skipped_fields)


def test_greenhouse_adapter_routes_to_review_on_missing_resume_input(
    monkeypatch, tmp_path, fake_page, fake_browser,
):
    """If the resume file input can't be located, route to review."""
    import asyncio
    from adapters.deterministic import greenhouse as gh_mod
    from adapters.base import SubmissionContext

    survey = {
        "first_name_present": True, "last_name_present": True,
        "email_present": True, "phone_present": True,
        "resume_present": True, "cover_letter_present": False,
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
        stagehand_session=fake_browser(survey=survey),
        page=fake_page(file_inputs_exist=False),  # ← no file input found
        attempt_n=1,
    )

    result = asyncio.run(gh_mod.GreenhouseAdapter().run(ctx))
    assert result.recommend == "needs_review"
    assert any(s.label == "resume" for s in result.skipped_fields)


def test_greenhouse_adapter_required_custom_q_routes_to_review(
    monkeypatch, tmp_path, fake_page, fake_browser,
):
    """A required custom question we can't answer drops confidence to review."""
    import asyncio
    from adapters.deterministic import greenhouse as gh_mod
    from adapters.base import SubmissionContext

    survey = {
        "first_name_present": True, "last_name_present": True,
        "email_present": True, "phone_present": True,
        "resume_present": True, "cover_letter_present": False,
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
        return {
            "classification": "required_by_form",
            "decision": "skip",
            "reason": "no mapping",
        }

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
        stagehand_session=fake_browser(survey=survey),
        page=fake_page(),
        attempt_n=1,
    )

    result = asyncio.run(gh_mod.GreenhouseAdapter().run(ctx))
    assert result.recommend == "needs_review"
    assert any(s.reason.startswith("required custom question")
               for s in result.skipped_fields)


def test_lever_adapter_full_name_variant(
    monkeypatch, tmp_path, fake_page, fake_browser,
):
    """Lever board with single full-name field + cover letter textarea should
    auto_submit at 0.90+ with all required fields."""
    import asyncio
    from adapters.deterministic import lever as lv_mod
    from adapters.base import SubmissionContext

    survey = {
        "full_name_present": True,
        "first_name_present": False, "last_name_present": False,
        "email_present": True, "phone_present": True,
        "resume_present": True, "cover_letter_textarea_present": True,
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
        stagehand_session=fake_browser(survey=survey),
        page=fake_page(),
        attempt_n=1,
    )

    result = asyncio.run(lv_mod.LeverAdapter().run(ctx))
    assert result.recommend == "auto_submit"
    assert result.confidence >= 0.90
    assert any(f.label == "full name" for f in result.filled_fields)
    # Textarea fill should have been recorded too
    assert any("cover letter" in f.label for f in result.filled_fields)


def test_ashby_adapter_missing_location_routes_to_review(
    monkeypatch, tmp_path, fake_page, fake_browser,
):
    """Ashby counts location as a core field — missing it should route to review."""
    import asyncio
    from adapters.deterministic import ashby as ab_mod
    from adapters.base import SubmissionContext

    survey = {
        "full_name_present": False,
        "first_name_present": True, "last_name_present": True,
        "email_present": True, "phone_present": True,
        "location_present": True,  # form has it, but applicant profile has no location
        "resume_present": True, "cover_letter_textarea_present": False,
        "custom_questions": [],
    }

    async def fake_extract(sess, instruction, schema, *, page=None): return survey
    async def fake_act(sess, input, *, page=None): return {"message": "ok"}

    monkeypatch.setattr(ab_mod, "sh_extract", fake_extract)
    import adapters._common as cmn
    monkeypatch.setattr(cmn, "sh_act", fake_act)

    # The base fake_page already implements wait_for_load_state as a no-op
    # (see tests/conftest.py::_FakePage), so no test-local subclass is needed.
    page = fake_page()

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
        stagehand_session=fake_browser(survey=survey),
        page=page,
        attempt_n=1,
    )

    result = asyncio.run(ab_mod.AshbyAdapter().run(ctx))
    assert result.recommend == "needs_review"
    assert any(s.label == "location" for s in result.skipped_fields)


def test_truly_optional_question_classified_and_skipped(monkeypatch):
    """Three-tier policy: truly_optional classification ⇒ skip without act().

    The LLM classifier decides the question is truly_optional (e.g., "How do
    you pronounce your name?") and we honor that with a skip, regardless of
    what the decision/answer fields say. No form-filling act() call fires.
    """
    import asyncio
    import adapters._common as cmn
    from adapters.base import SubmissionContext, SubmissionResult

    extract_calls: list = []
    act_calls: list = []

    async def fake_extract(sess, instruction, schema, *, page=None):
        extract_calls.append(instruction)
        # LLM correctly classifies the pronunciation question as optional.
        return {
            "classification": "truly_optional",
            "decision": "skip",
            "reason": "name pronunciation is opt-in demographic data",
        }
    async def fake_act(sess, input, *, page=None):
        act_calls.append(input)
        return {"message": "ok"}

    monkeypatch.setattr(cmn, "sh_extract", fake_extract)
    monkeypatch.setattr(cmn, "sh_act", fake_act)

    result = SubmissionResult()
    ctx = SubmissionContext(
        job={"id": "jq", "title": "Eng", "applicant_profile": {}},
        resume_pdf_path=Path("/tmp/r.pdf"),
        cover_letter_pdf_path=Path("/tmp/c.pdf"),
        cover_letter_text="",
        application_url="https://example.com",
        stagehand_session=SimpleNamespace(),
        page=SimpleNamespace(),
        attempt_n=1,
    )
    optional_q = {"label": "How do you pronounce your name?", "kind": "text", "required": False}
    asyncio.run(cmn.handle_custom_question(
        sess=ctx.stagehand_session, page=ctx.page, result=result,
        ctx=ctx, q=optional_q, ats_name="Greenhouse",
    ))
    # Classify extract fires once, but NO act() because truly_optional skips.
    assert len(extract_calls) == 1
    assert act_calls == []
    assert len(result.skipped_fields) == 1
    assert result.skipped_fields[0].reason.startswith("truly optional")


def test_effectively_required_question_answered(monkeypatch):
    """Three-tier policy: effectively_required + confident answer ⇒ filled."""
    import asyncio
    import adapters._common as cmn
    from adapters.base import SubmissionContext, SubmissionResult

    act_calls: list = []
    async def fake_extract(sess, instruction, schema, *, page=None):
        return {
            "classification": "effectively_required",
            "decision": "answer",
            "answer": "No",
            "reason": "applicant is a US citizen per profile",
        }
    async def fake_act(sess, input, *, page=None):
        act_calls.append(input); return {"message": "ok"}

    monkeypatch.setattr(cmn, "sh_extract", fake_extract)
    monkeypatch.setattr(cmn, "sh_act", fake_act)

    result = SubmissionResult()
    ctx = SubmissionContext(
        job={"id": "jq", "title": "Eng",
             "applicant_profile": {"work_authorization": "US citizen"}},
        resume_pdf_path=Path("/tmp/r.pdf"),
        cover_letter_pdf_path=Path("/tmp/c.pdf"),
        cover_letter_text="I am a US citizen, no sponsorship needed.",
        application_url="https://example.com",
        stagehand_session=SimpleNamespace(),
        page=SimpleNamespace(),
        attempt_n=1,
    )
    q = {"label": "Will you require visa sponsorship?", "kind": "radio", "required": False}
    asyncio.run(cmn.handle_custom_question(
        sess=ctx.stagehand_session, page=ctx.page, result=result,
        ctx=ctx, q=q, ats_name="Greenhouse",
    ))
    assert len(act_calls) == 1
    assert len(result.filled_fields) == 1
    assert result.filled_fields[0].value == "No"
    # Effectively-required skips don't drop confidence; this one didn't skip.
    assert result.skipped_fields == []


def test_effectively_required_skip_does_not_route_to_review(monkeypatch):
    """If the LLM can't confidently answer an effectively-required question,
    we skip it — but the resulting skip reason must NOT start with
    'required custom question' so score_and_recommend doesn't drop us
    into review for a question the form never marked as required."""
    import asyncio
    import adapters._common as cmn
    from adapters.base import SubmissionContext, SubmissionResult

    async def fake_extract(sess, instruction, schema, *, page=None):
        return {
            "classification": "effectively_required",
            "decision": "skip",
            "reason": "applicant has not stated relocation willingness",
        }
    async def fake_act(sess, input, *, page=None): return {"message": "ok"}

    monkeypatch.setattr(cmn, "sh_extract", fake_extract)
    monkeypatch.setattr(cmn, "sh_act", fake_act)

    result = SubmissionResult()
    ctx = SubmissionContext(
        job={"id": "jq", "title": "Eng", "applicant_profile": {}},
        resume_pdf_path=Path("/tmp/r.pdf"),
        cover_letter_pdf_path=Path("/tmp/c.pdf"),
        cover_letter_text="",
        application_url="https://example.com",
        stagehand_session=SimpleNamespace(),
        page=SimpleNamespace(),
        attempt_n=1,
    )
    q = {"label": "Are you open to relocation?", "kind": "radio", "required": False}
    asyncio.run(cmn.handle_custom_question(
        sess=ctx.stagehand_session, page=ctx.page, result=result,
        ctx=ctx, q=q, ats_name="Greenhouse",
    ))
    assert len(result.skipped_fields) == 1
    reason = result.skipped_fields[0].reason
    assert reason.startswith("effectively-required custom question")
    # Critical: must NOT collide with the 'required custom question' prefix
    # that score_and_recommend uses to drop confidence.
    assert not reason.startswith("required custom question")


def test_applicant_fields_surfaces_expanded_profile_keys():
    """#18: applicant_fields() must expose the seven new profile keys the
    classifier needs to answer effectively-required questions."""
    from adapters._common import applicant_fields

    job = {
        "id": "jf",
        "title": "Eng",
        "applicant_profile": {
            "first_name": "Vishal",
            "last_name": "Pathak",
            "email": "v@example.com",
            "phone": "555",
            "work_authorization": "us_citizen",
            "visa_sponsorship_needed": "no",
            "earliest_start_date": "as early as possible",
            "relocation_willingness": "prefer remote/local",
            "in_person_willingness": "remote or hybrid",
            "ai_policy_ack": "transparent use with human in the loop",
            "previous_interview_with_company": {"anthropic": False, "stripe": True},
        },
    }
    app = applicant_fields(job)
    assert app["work_authorization"]      == "us_citizen"
    assert app["visa_sponsorship_needed"] == "no"
    assert app["earliest_start_date"]     == "as early as possible"
    assert app["relocation_willingness"]  == "prefer remote/local"
    assert app["in_person_willingness"]   == "remote or hybrid"
    assert app["ai_policy_ack"].startswith("transparent")
    # Prior-interview dict is flattened into a human-readable summary; only
    # the True-valued companies should surface.
    assert "stripe" in app["previous_interview_summary"]
    assert "anthropic" not in app["previous_interview_summary"]


def test_applicant_fields_prior_interview_all_false():
    """If the candidate hasn't interviewed anywhere, the summary reports so
    — empty string would leave the LLM guessing."""
    from adapters._common import applicant_fields

    app = applicant_fields({
        "applicant_profile": {
            "previous_interview_with_company": {"anthropic": False, "stripe": False},
        }
    })
    assert app["previous_interview_summary"] == "no prior interviews with any listed company"


def test_applicant_fields_prior_interview_missing():
    """Missing key → empty string (not crash). Prompt template will render
    '(not specified)' in that slot."""
    from adapters._common import applicant_fields

    app = applicant_fields({"applicant_profile": {}})
    assert app["previous_interview_summary"] == ""


def test_custom_question_prompt_includes_profile_facts(monkeypatch):
    """The classifier prompt must include the applicant profile facts inline,
    so an LLM can answer e.g. 'Do you need visa sponsorship?' from
    applicant_profile.visa_sponsorship_needed = 'no'."""
    import asyncio
    import adapters._common as cmn
    from adapters.base import SubmissionContext, SubmissionResult

    captured_instruction: list[str] = []

    async def fake_extract(sess, instruction, schema, *, page=None):
        captured_instruction.append(instruction)
        return {
            "classification": "effectively_required",
            "decision": "answer",
            "answer": "No",
            "reason": "applicant is a US citizen, no sponsorship needed",
        }
    async def fake_act(sess, input, *, page=None): return {"message": "ok"}

    monkeypatch.setattr(cmn, "sh_extract", fake_extract)
    monkeypatch.setattr(cmn, "sh_act", fake_act)

    result = SubmissionResult()
    ctx = SubmissionContext(
        job={"id": "jp", "title": "SWE",
             "applicant_profile": {
                 "work_authorization": "us_citizen",
                 "visa_sponsorship_needed": "no",
                 "relocation_willingness": "prefer remote/local",
                 "ai_policy_ack": "human in the loop",
             }},
        resume_pdf_path=Path("/tmp/r.pdf"),
        cover_letter_pdf_path=Path("/tmp/c.pdf"),
        cover_letter_text="",
        application_url="https://example.com",
        stagehand_session=SimpleNamespace(),
        page=SimpleNamespace(),
        attempt_n=1,
    )
    q = {"label": "Do you require visa sponsorship?", "kind": "radio", "required": False}
    asyncio.run(cmn.handle_custom_question(
        sess=ctx.stagehand_session, page=ctx.page, result=result,
        ctx=ctx, q=q, ats_name="Greenhouse",
    ))
    assert captured_instruction, "classifier extract() was never called"
    prompt = captured_instruction[0]
    # All seven expanded-profile facts must be rendered into the prompt.
    assert "work_authorization:" in prompt
    assert "us_citizen" in prompt
    assert "visa_sponsorship_needed:" in prompt
    assert "relocation_willingness:" in prompt
    assert "ai_policy_acknowledgement:" in prompt
    assert "human in the loop" in prompt
    # Unspecified keys get a placeholder instead of blank.
    assert "(not specified)" in prompt


def test_custom_question_phase_cap(monkeypatch):
    """Hard cap on processed questions: after CUSTOM_Q_MAX (12), remaining
    questions are flushed as skips and recommend drops to needs_review."""
    import asyncio
    import adapters._common as cmn
    from adapters.base import SubmissionContext, SubmissionResult

    async def fake_extract(sess, instruction, schema, *, page=None):
        return {
            "classification": "truly_optional",
            "decision": "skip",
            "reason": "policy",
        }
    async def fake_act(sess, input, *, page=None): return {"message": "ok"}

    monkeypatch.setattr(cmn, "sh_extract", fake_extract)
    monkeypatch.setattr(cmn, "sh_act", fake_act)

    result = SubmissionResult()
    ctx = SubmissionContext(
        job={"id": "jc", "title": "Eng", "applicant_profile": {}},
        resume_pdf_path=Path("/tmp/r.pdf"),
        cover_letter_pdf_path=Path("/tmp/c.pdf"),
        cover_letter_text="",
        application_url="https://example.com",
        stagehand_session=SimpleNamespace(),
        page=SimpleNamespace(),
        attempt_n=1,
    )
    questions = [
        {"label": f"Optional question {i}", "kind": "text", "required": False}
        for i in range(20)  # 20 > CUSTOM_Q_MAX (12)
    ]
    asyncio.run(cmn.handle_custom_questions(
        sess=ctx.stagehand_session, page=ctx.page, result=result,
        ctx=ctx, questions=questions, ats_name="Greenhouse",
        max_questions=12,
    ))
    # 12 classified-and-skipped + 8 flushed with "cap" abort reason.
    assert len(result.skipped_fields) == 20
    cap_skipped = [s for s in result.skipped_fields if "cap" in s.reason]
    assert len(cap_skipped) == 8
    assert result.recommend == "needs_review"
    assert "cap" in result.recommend_reason


def test_custom_question_phase_budget(monkeypatch):
    """Phase wall-clock budget: if processing exceeds the budget, remaining
    questions land as skips with 'phase budget' reason and recommend drops
    to needs_review."""
    import asyncio
    import adapters._common as cmn
    from adapters.base import SubmissionContext, SubmissionResult

    async def slow_extract(sess, instruction, schema, *, page=None):
        # Simulate a 0.1s-per-call classifier against a 0.25s total budget.
        await asyncio.sleep(0.1)
        return {
            "classification": "truly_optional",
            "decision": "skip",
            "reason": "slow",
        }
    async def fake_act(sess, input, *, page=None): return {"message": "ok"}

    monkeypatch.setattr(cmn, "sh_extract", slow_extract)
    monkeypatch.setattr(cmn, "sh_act", fake_act)

    result = SubmissionResult()
    ctx = SubmissionContext(
        job={"id": "jb", "title": "Eng", "applicant_profile": {}},
        resume_pdf_path=Path("/tmp/r.pdf"),
        cover_letter_pdf_path=Path("/tmp/c.pdf"),
        cover_letter_text="",
        application_url="https://example.com",
        stagehand_session=SimpleNamespace(),
        page=SimpleNamespace(),
        attempt_n=1,
    )
    questions = [
        {"label": f"Q{i}", "kind": "text", "required": False} for i in range(10)
    ]
    asyncio.run(cmn.handle_custom_questions(
        sess=ctx.stagehand_session, page=ctx.page, result=result,
        ctx=ctx, questions=questions, ats_name="Greenhouse",
        phase_budget_s=0.25,
    ))
    # 2-3 should have been processed before budget check fires; the rest
    # flushed. Exact count is timing-dependent but we assert the outcome.
    budget_skipped = [s for s in result.skipped_fields if "phase budget" in s.reason]
    assert len(budget_skipped) >= 1
    assert result.recommend == "needs_review"
    assert "phase budget" in result.recommend_reason


def test_router_falls_back_to_generic_for_unknown_kind():
    """#11 / Milestone 6: unknown ats_kind resolves to the generic adapter
    now that adapters.generic_stagehand is wired up.

    The earlier test_router_unknown_falls_back_when_generic_exists installs
    a fake 'generic' in _REGISTRY to test the mechanism in isolation, and
    that assignment persists across tests. Pin the real class explicitly
    so this test verifies the real wiring regardless of ordering.
    """
    import router
    from adapters.generic_stagehand import GenericStagehandAdapter
    router._REGISTRY["generic"] = GenericStagehandAdapter
    adapter = router.get_adapter("workday")
    assert adapter.name == "generic"
    assert isinstance(adapter, GenericStagehandAdapter)


def test_generic_adapter_happy_path(monkeypatch, tmp_path, fake_page):
    """Generic adapter with agent reporting full success → needs_review 0.75.

    The generic fallback caps at needs_review by design — agent mode has no
    mechanical guarantee every required field was filled, so we always route
    to human review on the replay.
    """
    import asyncio
    from adapters import generic_stagehand as gs_mod
    from adapters.base import SubmissionContext

    async def fake_execute(sess, instruction, *, max_steps=25, model_name=None, timeout=300.0):
        # Agent reports structured success via its message field.
        assert "DO NOT click the final submit" in instruction
        assert "us_citizen" in instruction
        return {"message": "Filled all 9 fields; form now on review page."}

    async def fake_report_extract(sess, instruction, schema, *, page=None, timeout=60.0):
        return {
            "fields_filled": [
                {"label": "First name", "value": "Vishal"},
                {"label": "Last name", "value": "Pathak"},
                {"label": "Work authorization", "value": "US citizen"},
            ],
            "fields_skipped": [
                {"label": "Gender", "reason": "voluntary disclosure"},
            ],
            "missing_required": [],
            "reached_submit_step": True,
        }

    monkeypatch.setattr(gs_mod, "sh_execute", fake_execute)
    monkeypatch.setattr(gs_mod, "sh_extract", fake_report_extract)

    resume = tmp_path / "r.pdf"; resume.write_bytes(b"%PDF")
    cover  = tmp_path / "c.pdf"; cover.write_bytes(b"%PDF")

    ctx = SubmissionContext(
        job={"id": "wd1", "title": "SWE",
             "applicant_profile": {
                 "first_name": "Vishal", "last_name": "Pathak",
                 "email": "v@example.com", "phone": "555",
                 "work_authorization": "us_citizen",
                 "visa_sponsorship_needed": "no",
             }},
        resume_pdf_path=resume,
        cover_letter_pdf_path=cover,
        cover_letter_text="",
        application_url="https://workday.myexampleco.com/en-US/jobs/123",
        stagehand_session=SimpleNamespace(),
        page=fake_page(),
        attempt_n=1,
    )

    result = asyncio.run(gs_mod.GenericStagehandAdapter().run(ctx))
    # Always needs_review; but a successful agent run hits 0.75 not 0.55.
    assert result.recommend == "needs_review"
    assert result.confidence == 0.75
    assert "agent reports all fields filled" in result.recommend_reason
    # Filled fields from report are recorded at 0.70 confidence (agent-level).
    assert any(f.label == "Work authorization" and f.confidence == 0.70
               for f in result.filled_fields)
    # Agent-skipped demographic field shows up with "agent skipped:" prefix.
    assert any(s.reason.startswith("agent skipped:") and s.label == "Gender"
               for s in result.skipped_fields)
    # No "required custom question" skip when agent reports no missing fields.
    assert not any(s.reason.startswith("required custom question")
                   for s in result.skipped_fields)
    # agent_reasoning captured for the review packet.
    assert result.agent_reasoning is not None
    assert "Filled all 9 fields" in result.agent_reasoning


def test_generic_adapter_missing_required_drops_confidence(monkeypatch, tmp_path, fake_page):
    """Generic adapter: agent reports missing required field(s) → 0.55 needs_review.

    The critical invariant: missing_required labels land with the
    'required custom question' prefix so score consumers can count them.
    """
    import asyncio
    from adapters import generic_stagehand as gs_mod
    from adapters.base import SubmissionContext

    async def fake_execute(sess, instruction, *, max_steps=25, model_name=None, timeout=300.0):
        return {"message": "Stopped; two required fields lack profile data."}

    async def fake_report_extract(sess, instruction, schema, *, page=None, timeout=60.0):
        return {
            "fields_filled": [{"label": "Email", "value": "v@example.com"}],
            "fields_skipped": [],
            "missing_required": [
                "What is your expected salary?",
                "Where did you go to high school?",
            ],
            "reached_submit_step": False,
        }

    monkeypatch.setattr(gs_mod, "sh_execute", fake_execute)
    monkeypatch.setattr(gs_mod, "sh_extract", fake_report_extract)

    resume = tmp_path / "r.pdf"; resume.write_bytes(b"%PDF")
    cover  = tmp_path / "c.pdf"; cover.write_bytes(b"%PDF")

    ctx = SubmissionContext(
        job={"id": "ic1", "title": "SWE",
             "applicant_profile": {"email": "v@example.com"}},
        resume_pdf_path=resume,
        cover_letter_pdf_path=cover,
        cover_letter_text="",
        application_url="https://careers-myexampleco.icims.com/jobs/123",
        stagehand_session=SimpleNamespace(),
        page=fake_page(),
        attempt_n=1,
    )

    result = asyncio.run(gs_mod.GenericStagehandAdapter().run(ctx))
    assert result.recommend == "needs_review"
    assert result.confidence == 0.55
    assert "2 required field" in result.recommend_reason
    missing = [s for s in result.skipped_fields
               if s.reason.startswith("required custom question")]
    assert len(missing) == 2
    assert {s.label for s in missing} == {
        "What is your expected salary?",
        "Where did you go to high school?",
    }


def test_generic_adapter_execute_failure_aborts(monkeypatch, tmp_path, fake_page):
    """Generic adapter: sh_execute raising is a hard abort, not a silent skip."""
    import asyncio
    from adapters import generic_stagehand as gs_mod
    from adapters.base import SubmissionContext

    async def fake_execute(sess, instruction, *, max_steps=25, model_name=None, timeout=300.0):
        raise TimeoutError("stagehand.execute timed out after 300s")

    async def fake_report_extract(sess, instruction, schema, *, page=None, timeout=60.0):
        raise AssertionError("report extract should not run when execute fails")

    monkeypatch.setattr(gs_mod, "sh_execute", fake_execute)
    monkeypatch.setattr(gs_mod, "sh_extract", fake_report_extract)

    resume = tmp_path / "r.pdf"; resume.write_bytes(b"%PDF")
    cover  = tmp_path / "c.pdf"; cover.write_bytes(b"%PDF")

    ctx = SubmissionContext(
        job={"id": "sr1", "title": "SWE", "applicant_profile": {}},
        resume_pdf_path=resume,
        cover_letter_pdf_path=cover,
        cover_letter_text="",
        application_url="https://jobs.smartrecruiters.com/x/123",
        stagehand_session=SimpleNamespace(),
        page=fake_page(),
        attempt_n=1,
    )

    result = asyncio.run(gs_mod.GenericStagehandAdapter().run(ctx))
    assert result.recommend == "abort"
    assert result.error is not None
    assert "timed out" in result.error


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
