"""tests/test_prefill_attempts_audit.py — Path-A audit-row plumbing.

Drives ``jobpipe.tailor.pipeline.process_prefill_requested_jobs`` against
a fake Supabase row, fake browser, fake applicant, and fake terminal
``input()``, then asserts the ``application_attempts`` lifecycle:

  - ``next_attempt_n`` is called per job.
  - ``open_attempt`` is called with the picked applicant's ``name``.
  - On the success branch, ``close_attempt`` is called with
    ``outcome="prefilled"`` (P0 rename — a clean pre-fill close, not an
    actual ATS submission) and a ``notes`` dict carrying the
    ``prefill_screenshot_path`` key.
  - On the failure branch, ``close_attempt`` is called with
    ``outcome="failed"`` and an ``error`` key in ``notes``.
  - In both branches, the attempt row is closed BEFORE the terminal
    ``input()`` block so the dashboard sees the outcome immediately.

Stays offline — no Supabase, Browserbase, Anthropic, or real Playwright
calls. Every cross-boundary symbol is monkeypatched on the
``jobpipe.tailor.pipeline`` module surface (where the ``from … import``
statements at the top of the file already bind them) plus the lazy
imports inside the function body (Playwright via ``sys.modules``,
``ats_detect`` / ``url_resolver`` on their source modules).
"""

from __future__ import annotations

import sys
from pathlib import Path
from types import SimpleNamespace

import pytest


# ── Required env (jobpipe.submit.config fail-louds without these) ──────────


@pytest.fixture(autouse=True)
def _required_env(monkeypatch):
    """``jobpipe.submit.config`` raises at import if these are missing.

    The pipeline module imports from jobpipe.shared.ats_detect, which
    transitively pulls submit.config in via the prepare_dom.universal
    lazy import. Setting placeholders keeps imports cheap.
    """
    for k, v in {
        "SUPABASE_URL": "https://example.supabase.co",
        "SUPABASE_KEY": "anon-test",
        "SUPABASE_SERVICE_ROLE_KEY": "service-test",
        "BROWSERBASE_API_KEY": "bb-test",
        "BROWSERBASE_PROJECT_ID": "bb-proj-test",
        "ANTHROPIC_API_KEY": "sk-test",
        # Force the cookieless headless launch so the simple fake chromium
        # (which only implements ``launch``) is exercised — this test is
        # about the attempts-row lifecycle, not the persistent-profile path.
        "HEADLESS": "1",
    }.items():
        monkeypatch.setenv(k, v)
    monkeypatch.delenv("JOBPIPE_BROWSER_CDP", raising=False)


# ── Fake Playwright surface ────────────────────────────────────────────────


class _FakePage:
    """Minimal sync Playwright page double."""

    def __init__(self):
        self._goto_called = False
        self.closed = False

    def goto(self, url, wait_until=None, timeout=None):
        self._goto_called = True

    def wait_for_load_state(self, *args, **kwargs):
        return None

    def screenshot(self, full_page=False):
        return b"\x89PNG_FAKE"

    def close(self):
        self.closed = True


class _FakeBrowser:
    def __init__(self, page: _FakePage):
        self._page = page

    def new_context(self, **kwargs):
        return SimpleNamespace(new_page=lambda: self._page)

    def close(self):
        return None


class _FakeChromium:
    def __init__(self, browser: _FakeBrowser):
        self._browser = browser

    def launch(self, headless=False):
        return self._browser


class _FakePW:
    def __init__(self, page: _FakePage):
        self.chromium = _FakeChromium(_FakeBrowser(page))


class _SyncPlaywrightCM:
    """Mimics ``with sync_playwright() as pw:``."""

    def __init__(self, page: _FakePage):
        self._pw = _FakePW(page)

    def __enter__(self):
        return self._pw

    def __exit__(self, *exc):
        return False


def _install_fake_playwright(monkeypatch, page: _FakePage):
    fake_pw_pkg = type(sys)("playwright")
    fake_sync = type(sys)("playwright.sync_api")

    fake_sync.sync_playwright = lambda: _SyncPlaywrightCM(page)
    # The pipeline lazy-imports UniversalApplicant, which transitively pulls
    # ``submit/adapters/browser_tools.py`` — that module does
    # ``from playwright.sync_api import Page, TimeoutError as
    # PlaywrightTimeoutError`` at top level. Expose synthetic stand-ins so
    # the import resolves without the real Playwright SDK.
    fake_sync.Page = type("Page", (), {})
    fake_sync.Browser = type("Browser", (), {})
    fake_sync.TimeoutError = type("TimeoutError", (Exception,), {})
    fake_pw_pkg.sync_api = fake_sync

    monkeypatch.setitem(sys.modules, "playwright", fake_pw_pkg)
    monkeypatch.setitem(sys.modules, "playwright.sync_api", fake_sync)


# ── Fake applicant (greenhouse-shaped, NOT a UniversalApplicant) ───────────


class _FakeApplicant:
    """Stands in for a prepare_dom adapter chosen by ats_detect.get_applicant.

    The pipeline isinstance-checks against ``UniversalApplicant`` and falls
    through to ``applicant.fill_form(...)`` for everything else. We keep
    that branch by NOT subclassing UniversalApplicant.
    """

    name = "greenhouse"

    def __init__(self, success: bool = True, notes: str = ""):
        self._success = success
        self._notes = notes

    def fill_form(self, page, job, resume_path=None, cover_letter_path=None):
        if self._success:
            return {
                "success": True,
                "fields_filled": ["First Name", "Email"],
                "notes": "Filled 2 fields",
                "screenshot_path": None,
            }
        return {
            "success": False,
            "notes": self._notes or "pre-fill did not complete cleanly",
            "fields_filled": [],
        }


# ── Common scaffolding ────────────────────────────────────────────────────


def _stub_pipeline_surface(monkeypatch, *, applicant, call_log: list,
                           tmp_resume_path: Path):
    """Patch every cross-boundary name the pipeline binds at module top.

    Returns the pipeline module so callers can also patch
    ``get_prefill_requested_jobs`` to seed the fake row. Mutates
    ``call_log`` in-order as the pipeline runs. Also patches
    ``url_resolver`` and ``ats_detect`` (lazy-imported inside the
    function) on their source modules.
    """
    # Trigger the tailor sys.path bootstrap so ``url_resolver`` resolves.
    from jobpipe.tailor import pipeline as p

    # Audit-row plumbing.
    monkeypatch.setattr(
        p, "next_attempt_n",
        lambda jid: call_log.append(("next_attempt_n", jid)) or 1,
    )

    def _open_attempt(jid, n, adapter):
        call_log.append(("open_attempt", jid, n, adapter))
        return 4242

    monkeypatch.setattr(p, "open_attempt", _open_attempt)

    def _close_attempt(attempt_id, *, outcome, **kw):
        call_log.append(("close_attempt", attempt_id, outcome, kw))

    monkeypatch.setattr(p, "close_attempt", _close_attempt)

    # Status transitions and notifications — record only.
    monkeypatch.setattr(
        p, "mark_awaiting_submit",
        lambda *a, **kw: call_log.append(("mark_awaiting_submit", a, kw)),
    )
    monkeypatch.setattr(
        p, "mark_tailor_failed",
        lambda *a, **kw: call_log.append(("mark_tailor_failed", a, kw)),
    )
    monkeypatch.setattr(
        p, "send_awaiting_submit",
        lambda *a, **kw: call_log.append(("send_awaiting_submit",)),
    )
    monkeypatch.setattr(
        p, "send_failed",
        lambda *a, **kw: call_log.append(("send_failed",)),
    )

    # Assisted-manual hand-off — stubbed here (its own end-to-end behaviour is
    # covered by tests/test_prefill_assisted_handoff.py). This test only cares
    # that a non-success exit routes through the hand-off and the attempt row
    # still closes (as needs_review) before the input() block.
    def _handoff_stub(page, job, reason, unfilled=None, summary=None):
        call_log.append(("handoff", reason))
        return {
            "handoff": True,
            "materials_dir": "/tmp/handoff/x",
            "checklist": [],
            "application_notes": "ASSISTED-MANUAL",
            "reason": reason,
            "problems": [],
        }

    monkeypatch.setattr(p, "assisted_manual_handoff", _handoff_stub)

    # Part B verification write — record-only (no DB).
    monkeypatch.setattr(
        p, "record_prefill_verification",
        lambda jid, v, **kw: call_log.append(("record_verification", jid)),
    )

    # Storage.
    monkeypatch.setattr(p, "download_to_tmp", lambda key: tmp_resume_path)
    monkeypatch.setattr(
        p, "upload_prefill_screenshot",
        lambda jid, png_bytes: f"{jid}/prefill.png",
    )

    # Lazy imports inside the function body.
    import jobpipe.shared.ats_detect as ats_mod
    monkeypatch.setattr(ats_mod, "detect_ats", lambda url: "greenhouse")
    monkeypatch.setattr(ats_mod, "get_applicant", lambda url: applicant)

    import url_resolver  # available because pipeline import bootstrapped sys.path
    monkeypatch.setattr(
        url_resolver, "resolve_application_url",
        lambda url: {"resolved": url, "is_ats": True, "trail": [],
                     "notes": "ok"},
    )

    # Stop-and-wait advance (Part B): the loop now polls the row instead of
    # blocking on input(). Returning a terminal status resolves the wait on
    # the first poll; record the poll so tests can assert it fires AFTER
    # close_attempt (replacing the old input() ordering marker).
    monkeypatch.setattr(
        p, "get_job",
        lambda jid: call_log.append(("wait_poll", jid)) or {"id": jid, "status": "applied"},
    )

    return p


def _make_job(job_id: str = "test-job-audit") -> dict:
    return {
        "id": job_id,
        "company": "TestCo",
        "title": "Test Engineer",
        "url": "https://boards.greenhouse.io/testco/jobs/1",
        "submission_url": "https://boards.greenhouse.io/testco/jobs/1",
        "application_url": "https://boards.greenhouse.io/testco/jobs/1",
        "resume_pdf_path": f"{job_id}/resume.pdf",
        "cover_letter_path": "Dear Team,\n\nI am writing about your role.",
        "form_answers": {"first_name": "Vishal"},
    }


@pytest.fixture
def tmp_resume_pdf(tmp_path):
    """Tiny fake PDF on disk so the post-download Path() exists check passes."""
    p = tmp_path / "fake_resume.pdf"
    p.write_bytes(b"%PDF-fake-for-audit-test")
    return p


# ── Tests ────────────────────────────────────────────────────────────────


def test_success_branch_closes_attempt_with_prefilled_before_input(
    monkeypatch, tmp_resume_pdf,
):
    call_log: list = []
    job = _make_job("audit-success")
    fake_page = _FakePage()
    _install_fake_playwright(monkeypatch, fake_page)

    p = _stub_pipeline_surface(
        monkeypatch,
        applicant=_FakeApplicant(success=True),
        call_log=call_log,
        tmp_resume_path=tmp_resume_pdf,
    )
    monkeypatch.setattr(p, "get_prefill_requested_jobs", lambda: [job])

    p.process_prefill_requested_jobs()

    ops = [entry[0] for entry in call_log]

    # Order invariant: next_attempt_n → open_attempt → close_attempt →
    # wait_poll (the stop-and-wait advance, replacing the old input() block).
    assert ops.index("next_attempt_n") < ops.index("open_attempt") \
        < ops.index("close_attempt") < ops.index("wait_poll"), (
        f"audit-row sequence wrong; ops={ops}"
    )
    # Advance closed the tab once the human flipped the row to applied.
    assert fake_page.closed is True

    # open_attempt invoked with the applicant's name attribute.
    open_call = next(c for c in call_log if c[0] == "open_attempt")
    assert open_call[1] == "audit-success"
    assert open_call[2] == 1
    assert open_call[3] == "greenhouse"

    # close_attempt: outcome="prefilled", notes contains the screenshot key.
    close_call = next(c for c in call_log if c[0] == "close_attempt")
    _, attempt_id, outcome, kwargs = close_call
    assert attempt_id == 4242
    assert outcome == "prefilled"
    notes = kwargs.get("notes") or {}
    assert notes.get("prefill_screenshot_path") == "audit-success/prefill.png"
    assert notes.get("filled_fields") == ["First Name", "Email"]
    assert notes.get("notes") == "Filled 2 fields"

    # Status transition fired; failure path did NOT fire.
    assert any(o == "mark_awaiting_submit" for o in ops)
    assert not any(o == "mark_tailor_failed" for o in ops)


def test_failure_branch_degrades_to_assisted_manual_handoff(
    monkeypatch, tmp_resume_pdf,
):
    """A non-success adapter result is no longer a bare failure: it routes
    through ``assisted_manual_handoff`` and the attempt row closes as
    ``needs_review`` (carrying the diagnostic screenshot) before input()."""
    call_log: list = []
    job = _make_job("audit-fail")
    fake_page = _FakePage()
    _install_fake_playwright(monkeypatch, fake_page)

    p = _stub_pipeline_surface(
        monkeypatch,
        applicant=_FakeApplicant(success=False, notes="no fields matched"),
        call_log=call_log,
        tmp_resume_path=tmp_resume_pdf,
    )
    monkeypatch.setattr(p, "get_prefill_requested_jobs", lambda: [job])

    p.process_prefill_requested_jobs()

    ops = [entry[0] for entry in call_log]

    # Ordering invariant: next_attempt_n → open_attempt → handoff →
    # close_attempt → wait_poll (stop-and-wait advance).
    assert ops.index("next_attempt_n") < ops.index("open_attempt") \
        < ops.index("handoff") < ops.index("close_attempt") \
        < ops.index("wait_poll"), (
        f"assisted-manual sequence wrong; ops={ops}"
    )
    assert fake_page.closed is True

    # Hand-off ran with the adapter's reason.
    handoff_call = next(c for c in call_log if c[0] == "handoff")
    assert handoff_call[1] == "no fields matched"

    # close_attempt: outcome="needs_review" (NOT "failed"), notes flag the
    # assisted-manual hand-off and carry the diagnostic screenshot.
    close_call = next(c for c in call_log if c[0] == "close_attempt")
    _, attempt_id, outcome, kwargs = close_call
    assert attempt_id == 4242
    assert outcome == "needs_review"
    notes = kwargs.get("notes") or {}
    assert notes.get("assisted_manual") is True
    assert notes.get("prefill_screenshot_path") == "audit-fail/prefill.png"
    assert notes.get("materials_dir") == "/tmp/handoff/x"

    # No bare failure: mark_tailor_failed must NOT fire for a non-success
    # adapter result once the tab is open.
    assert not any(o == "mark_tailor_failed" for o in ops)


def test_open_attempt_uses_correct_adapter_name_for_each_ats(
    monkeypatch, tmp_resume_pdf,
):
    """``open_attempt`` must thread the *applicant.name* — not an ATS string
    derived elsewhere. Catches a regression where someone hard-codes
    ``"greenhouse"`` instead of ``applicant.name`` (which would lose the
    distinction between the lever / ashby / universal handlers).
    """
    call_log: list = []
    job = _make_job("audit-lever")

    class _LeverApplicant(_FakeApplicant):
        name = "lever"

    fake_page = _FakePage()
    _install_fake_playwright(monkeypatch, fake_page)

    p = _stub_pipeline_surface(
        monkeypatch,
        applicant=_LeverApplicant(success=True),
        call_log=call_log,
        tmp_resume_path=tmp_resume_pdf,
    )
    monkeypatch.setattr(p, "get_prefill_requested_jobs", lambda: [job])

    p.process_prefill_requested_jobs()

    open_call = next(c for c in call_log if c[0] == "open_attempt")
    assert open_call[3] == "lever"


# ── Materials-hash gate (hygiene #3) ────────────────────────────────────────

def test_materials_hash_mismatch_routes_to_handoff_without_filling(
    monkeypatch, tmp_resume_pdf,
):
    """A materials_hash mismatch (resume/cover letter drifted since
    approval) must degrade to the assisted-manual hand-off BEFORE the
    adapter's fill_form ever runs — filling with stale materials would
    silently present outdated content for the human to submit."""
    call_log: list = []
    job = _make_job("audit-hash-mismatch")
    fake_page = _FakePage()
    _install_fake_playwright(monkeypatch, fake_page)

    fill_form_called: list = []

    class _TrackingApplicant(_FakeApplicant):
        def fill_form(self, *a, **kw):
            fill_form_called.append(True)
            return super().fill_form(*a, **kw)

    p = _stub_pipeline_surface(
        monkeypatch,
        applicant=_TrackingApplicant(success=True),
        call_log=call_log,
        tmp_resume_path=tmp_resume_pdf,
    )
    monkeypatch.setattr(p, "get_prefill_requested_jobs", lambda: [job])
    monkeypatch.setattr(
        p, "verify_materials_hash",
        lambda job, resume_bytes, cover_letter_text: False,
    )

    p.process_prefill_requested_jobs()

    ops = [entry[0] for entry in call_log]
    assert "handoff" in ops
    assert not fill_form_called, "fill_form must not run after a materials_hash mismatch"

    handoff_call = next(c for c in call_log if c[0] == "handoff")
    assert "materials hash mismatch" in handoff_call[1]

    close_call = next(c for c in call_log if c[0] == "close_attempt")
    _, attempt_id, outcome, kwargs = close_call
    assert outcome == "needs_review"


def test_materials_hash_match_proceeds_to_fill(monkeypatch, tmp_resume_pdf):
    """A matching hash (the common case) must NOT block the fill."""
    call_log: list = []
    job = _make_job("audit-hash-match")
    fake_page = _FakePage()
    _install_fake_playwright(monkeypatch, fake_page)

    p = _stub_pipeline_surface(
        monkeypatch,
        applicant=_FakeApplicant(success=True),
        call_log=call_log,
        tmp_resume_path=tmp_resume_pdf,
    )
    monkeypatch.setattr(p, "get_prefill_requested_jobs", lambda: [job])
    monkeypatch.setattr(
        p, "verify_materials_hash",
        lambda job, resume_bytes, cover_letter_text: True,
    )

    p.process_prefill_requested_jobs()

    ops = [entry[0] for entry in call_log]
    assert "handoff" not in ops
    close_call = next(c for c in call_log if c[0] == "close_attempt")
    assert close_call[2] == "prefilled"
