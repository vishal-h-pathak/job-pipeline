"""tests/test_prefill_attempts_audit.py — Path-A audit-row plumbing.

Drives ``jobpipe.tailor.pipeline.process_prefill_requested_jobs`` against
a fake Supabase row, fake browser, fake applicant, and fake terminal
``input()``, then asserts the ``application_attempts`` lifecycle:

  - ``next_attempt_n`` is called per job.
  - ``open_attempt`` is called with the picked applicant's ``name``.
  - On the success branch, ``close_attempt`` is called with
    ``outcome="submitted"`` and a ``notes`` dict carrying the
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

import builtins
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
    }.items():
        monkeypatch.setenv(k, v)


# ── Fake Playwright surface ────────────────────────────────────────────────


class _FakePage:
    """Minimal sync Playwright page double."""

    def __init__(self):
        self._goto_called = False

    def goto(self, url, wait_until=None, timeout=None):
        self._goto_called = True

    def wait_for_load_state(self, *args, **kwargs):
        return None

    def screenshot(self, full_page=False):
        return b"\x89PNG_FAKE"


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
    # The failure branch persists ``prefill_screenshot_path`` on the
    # jobs row via a follow-up ``update_job_status`` call (parity with
    # the success path). Stub records the call shape; doesn't hit DB.
    monkeypatch.setattr(
        p, "update_job_status",
        lambda *a, **kw: call_log.append(("update_job_status", a, kw)),
    )
    monkeypatch.setattr(
        p, "send_awaiting_submit",
        lambda *a, **kw: call_log.append(("send_awaiting_submit",)),
    )
    monkeypatch.setattr(
        p, "send_failed",
        lambda *a, **kw: call_log.append(("send_failed",)),
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

    # input() block — must fire AFTER close_attempt.
    monkeypatch.setattr(
        builtins, "input",
        lambda *a, **kw: call_log.append(("input",)),
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


def test_success_branch_closes_attempt_with_submitted_before_input(
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

    # Order invariant: next_attempt_n → open_attempt → close_attempt → input.
    assert ops.index("next_attempt_n") < ops.index("open_attempt") \
        < ops.index("close_attempt") < ops.index("input"), (
        f"audit-row sequence wrong; ops={ops}"
    )

    # open_attempt invoked with the applicant's name attribute.
    open_call = next(c for c in call_log if c[0] == "open_attempt")
    assert open_call[1] == "audit-success"
    assert open_call[2] == 1
    assert open_call[3] == "greenhouse"

    # close_attempt: outcome="submitted", notes contains the screenshot key.
    close_call = next(c for c in call_log if c[0] == "close_attempt")
    _, attempt_id, outcome, kwargs = close_call
    assert attempt_id == 4242
    assert outcome == "submitted"
    notes = kwargs.get("notes") or {}
    assert notes.get("prefill_screenshot_path") == "audit-success/prefill.png"
    assert notes.get("filled_fields") == ["First Name", "Email"]
    assert notes.get("notes") == "Filled 2 fields"

    # Status transition fired; failure path did NOT fire.
    assert any(o == "mark_awaiting_submit" for o in ops)
    assert not any(o == "mark_tailor_failed" for o in ops)


def test_failure_branch_closes_attempt_with_failed_and_error_key(
    monkeypatch, tmp_resume_pdf,
):
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

    # Same ordering invariant on the failure branch.
    assert ops.index("next_attempt_n") < ops.index("open_attempt") \
        < ops.index("close_attempt") < ops.index("input"), (
        f"audit-row sequence wrong on failure branch; ops={ops}"
    )

    # close_attempt: outcome="failed", notes carries error AND screenshot
    # path (parity fix — failure branch must surface the same diagnostic
    # screenshot the success branch persists).
    close_call = next(c for c in call_log if c[0] == "close_attempt")
    _, attempt_id, outcome, kwargs = close_call
    assert attempt_id == 4242
    assert outcome == "failed"
    notes = kwargs.get("notes") or {}
    assert notes.get("error") == "no fields matched"
    assert notes.get("prefill_screenshot_path") == "audit-fail/prefill.png"

    # Failure branch must ALSO persist prefill_screenshot_path on the
    # jobs row via a follow-up update_job_status call (parity with the
    # success path's mark_awaiting_submit). Catch a regression where
    # someone reverts the row-side update and leaves only the
    # close_attempt notes update.
    update_call = next(
        (c for c in call_log if c[0] == "update_job_status"),
        None,
    )
    assert update_call is not None, (
        "failure branch must persist prefill_screenshot_path on the jobs row"
    )
    _, args, kwargs = update_call
    assert args[0] == "audit-fail"
    assert args[1] == "failed"
    assert kwargs.get("prefill_screenshot_path") == "audit-fail/prefill.png"

    # Ordering: mark_tailor_failed → update_job_status → close_attempt → input.
    assert ops.index("mark_tailor_failed") < ops.index("update_job_status") \
        < ops.index("close_attempt") < ops.index("input"), (
        f"failure-branch ordering wrong; ops={ops}"
    )

    # Status transition: mark_tailor_failed fired; mark_awaiting_submit did not.
    assert any(o == "mark_tailor_failed" for o in ops)
    assert not any(o == "mark_awaiting_submit" for o in ops)


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
