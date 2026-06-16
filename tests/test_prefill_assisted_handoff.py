"""tests/test_prefill_assisted_handoff.py — Part 2 wired into the pipeline.

Drives ``jobpipe.tailor.pipeline.process_prefill_requested_jobs`` and asserts
that every non-success prepare exit degrades to an assisted-manual hand-off
rather than a bare failure:

  - a non-success adapter result (e.g. the agent called ``queue_for_review``)
    → ``assisted_manual_handoff`` runs, the row lands on
    ``awaiting_human_submit``, the tab is NOT closed;
  - an adapter that *raises* → same: hand-off, tab open, no bare failure.

Runs the REAL ``assisted_manual_handoff`` (only its storage + db edges are
faked) so the wiring + status transition are exercised end-to-end. Headless
(``HEADLESS=1``) so the launch path uses the simple fake browser.
"""

from __future__ import annotations

import builtins
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest


@pytest.fixture(autouse=True)
def _env(monkeypatch, tmp_path):
    for k, v in {
        "SUPABASE_URL": "https://example.supabase.co",
        "SUPABASE_KEY": "anon-test",
        "SUPABASE_SERVICE_ROLE_KEY": "service-test",
        "BROWSERBASE_API_KEY": "bb-test",
        "BROWSERBASE_PROJECT_ID": "bb-proj-test",
        "ANTHROPIC_API_KEY": "sk-test",
        "HEADLESS": "1",  # use the simple cookieless launch fake
        "JOBPIPE_HANDOFF_DIR": str(tmp_path / "handoff"),
    }.items():
        monkeypatch.setenv(k, v)
    monkeypatch.delenv("JOBPIPE_BROWSER_CDP", raising=False)


class _FakePage:
    def __init__(self):
        self.closed = False
        self.url = "https://jobs.example.com/apply"

    def goto(self, url, wait_until=None, timeout=None):
        return None

    def wait_for_load_state(self, *a, **k):
        return None

    def screenshot(self, full_page=False):
        return b"\x89PNG_FAKE"

    def close(self):
        self.closed = True


class _FakeContext:
    def __init__(self, page):
        self._page = page
        self.closed = False

    def new_page(self):
        return self._page

    def close(self):
        self.closed = True


class _FakeBrowser:
    def __init__(self, page):
        self._ctx = _FakeContext(page)

    def new_context(self, **kwargs):
        return self._ctx

    def close(self):
        return None


class _FakeChromium:
    def __init__(self, page):
        self._browser = _FakeBrowser(page)

    def launch(self, **kwargs):
        return self._browser


class _PWcm:
    def __init__(self, page):
        self._pw = SimpleNamespace(chromium=_FakeChromium(page))

    def __enter__(self):
        return self._pw

    def __exit__(self, *exc):
        return False


def _install_fake_pw(monkeypatch, page):
    pkg = type(sys)("playwright")
    sync = type(sys)("playwright.sync_api")
    sync.sync_playwright = lambda: _PWcm(page)
    sync.Page = type("Page", (), {})
    sync.Browser = type("Browser", (), {})
    sync.TimeoutError = type("TimeoutError", (Exception,), {})
    pkg.sync_api = sync
    monkeypatch.setitem(sys.modules, "playwright", pkg)
    monkeypatch.setitem(sys.modules, "playwright.sync_api", sync)


def _job(job_id="ho-1"):
    return {
        "id": job_id,
        "company": "Acme",
        "title": "Engineer",
        "url": "https://boards.greenhouse.io/acme/jobs/1",
        "application_url": "https://boards.greenhouse.io/acme/jobs/1",
        "resume_pdf_path": f"{job_id}/resume.pdf",
        "cover_letter_pdf_path": f"{job_id}/cover_letter.pdf",
        "cover_letter_path": "Dear Acme,\n\nHello.\n",
        "form_answers": {"first_name": "Vishal"},
    }


def _wire(monkeypatch, *, applicant, tmp_path):
    """Patch the pipeline + handoff edges; return (pipeline, status_calls)."""
    from jobpipe.tailor import pipeline as p
    from jobpipe.submit import handoff as h

    monkeypatch.setattr(p, "next_attempt_n", lambda jid: 1)
    monkeypatch.setattr(p, "open_attempt", lambda jid, n, adapter: 99)
    closes: list = []
    monkeypatch.setattr(
        p, "close_attempt",
        lambda aid, *, outcome, **kw: closes.append((outcome, kw)),
    )
    monkeypatch.setattr(p, "mark_awaiting_submit", lambda *a, **k: None)
    monkeypatch.setattr(p, "mark_tailor_failed",
                        lambda *a, **k: closes.append(("mark_tailor_failed", a)))
    monkeypatch.setattr(p, "send_awaiting_submit", lambda *a, **k: None)
    monkeypatch.setattr(p, "send_failed", lambda *a, **k: None)
    monkeypatch.setattr(p, "download_to_tmp", lambda key: tmp_path / "resume_src.pdf")
    monkeypatch.setattr(p, "upload_prefill_screenshot", lambda jid, b: f"{jid}/p.png")
    (tmp_path / "resume_src.pdf").write_bytes(b"%PDF-resume")

    import jobpipe.shared.ats_detect as ats_mod
    monkeypatch.setattr(ats_mod, "detect_ats", lambda url: "greenhouse")
    monkeypatch.setattr(ats_mod, "get_applicant", lambda url: applicant)

    import url_resolver
    monkeypatch.setattr(
        url_resolver, "resolve_application_url",
        lambda url: {"resolved": url, "is_ats": True, "trail": [], "notes": "ok"},
    )
    monkeypatch.setattr(builtins, "input", lambda *a, **k: None)

    # Real handoff, faked edges.
    status_calls: list = []

    def _download_to_tmp(storage_path, suffix=None):
        src = tmp_path / f"dl_{len(status_calls)}_{Path(storage_path).name}"
        src.write_bytes(b"%PDF-" + storage_path.encode())
        return src

    monkeypatch.setattr(h, "download_to_tmp", _download_to_tmp)
    monkeypatch.setattr(
        h, "update_job_status",
        lambda jid, status, **extra: status_calls.append((jid, status, extra)),
    )
    return p, status_calls, closes


class _NonSuccessApplicant:
    name = "greenhouse"

    def fill_form(self, page, job, resume_path=None, cover_letter_path=None):
        return {
            "success": False,
            "review_reason": "agent queued for review: unsure on work auth",
            "uncertain_fields": ["work_authorization"],
        }


class _RaisingApplicant:
    name = "greenhouse"

    def fill_form(self, page, job, resume_path=None, cover_letter_path=None):
        raise RuntimeError("selector blew up")


def test_non_success_result_degrades_to_handoff(monkeypatch, tmp_path):
    page = _FakePage()
    _install_fake_pw(monkeypatch, page)
    p, status_calls, closes = _wire(
        monkeypatch, applicant=_NonSuccessApplicant(), tmp_path=tmp_path,
    )
    monkeypatch.setattr(p, "get_prefill_requested_jobs", lambda: [_job("ho-1")])

    p.process_prefill_requested_jobs()

    # Row landed on awaiting_human_submit via the real handoff (not failed).
    assert status_calls, "handoff did not mark the row"
    jid, status, extra = status_calls[-1]
    assert jid == "ho-1"
    assert status == "awaiting_human_submit"
    assert "ASSISTED-MANUAL" in extra["application_notes"].upper()
    assert "work_authorization" in extra["application_notes"]

    # Tab left OPEN.
    assert page.closed is False

    # Materials staged locally for drag-in.
    folder = tmp_path / "handoff" / "Acme_ho-1"
    assert (folder / "resume.pdf").exists()
    assert (folder / "cover_letter.txt").exists()

    # The attempt row was closed (not left dangling) and NOT as a bare failure.
    assert closes, "attempt row never closed"
    assert closes[-1][0] == "needs_review"
    assert not any(c[0] == "mark_tailor_failed" for c in closes)


def test_adapter_exception_degrades_to_handoff(monkeypatch, tmp_path):
    page = _FakePage()
    _install_fake_pw(monkeypatch, page)
    p, status_calls, closes = _wire(
        monkeypatch, applicant=_RaisingApplicant(), tmp_path=tmp_path,
    )
    monkeypatch.setattr(p, "get_prefill_requested_jobs", lambda: [_job("ho-2")])

    p.process_prefill_requested_jobs()

    assert status_calls, "handoff did not run on adapter exception"
    jid, status, extra = status_calls[-1]
    assert status == "awaiting_human_submit"
    assert "selector blew up" in extra["application_notes"]

    # Tab left OPEN even though the adapter threw.
    assert page.closed is False
    # Attempt row closed, not a bare failure.
    assert closes and closes[-1][0] == "needs_review"
