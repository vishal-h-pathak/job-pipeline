"""tests/test_submit_local_browser.py — persistent, logged-in browser (Part 1).

Covers ``jobpipe.submit.browser.local.open_browser_context`` — the single
launch-strategy decision shared by the prepare flow:

  - default (visible) → ``launch_persistent_context`` with the configured
    ``JOBPIPE_BROWSER_PROFILE`` so ATS logins persist across runs;
  - ``HEADLESS=1`` → falls back to the old ``launch()`` + ``new_context()``
    cookieless path (so the suite + CI keep working);
  - ``JOBPIPE_BROWSER_CDP`` set → ``connect_over_cdp`` to the user's
    already-running Chrome, reusing its existing context.

Plus one integration test that ``UniversalApplicant.apply`` routes its
browser creation through the helper (persistent context in visible mode).

Stays offline — fake Playwright surface, no real browser, no network.
"""

from __future__ import annotations

from types import SimpleNamespace

import pytest


# ── Fake Playwright surface ────────────────────────────────────────────────


class _FakeContext:
    def __init__(self, label: str):
        self.label = label
        self.closed = False
        self.pages_opened = 0

    def new_page(self):
        self.pages_opened += 1
        return SimpleNamespace(url="about:blank")

    def close(self):
        self.closed = True


class _FakeBrowser:
    def __init__(self, label: str, *, contexts=None):
        self.label = label
        self.closed = False
        self.contexts = contexts if contexts is not None else []
        self._new_context_kwargs = None

    def new_context(self, **kwargs):
        self._new_context_kwargs = kwargs
        ctx = _FakeContext(f"{self.label}:new_context")
        self.contexts.append(ctx)
        return ctx

    def close(self):
        self.closed = True


class _RecordingChromium:
    """Records which launch strategy the helper picked."""

    def __init__(self):
        self.calls: list[tuple] = []
        self.launch_browser = _FakeBrowser("launch")
        self.persistent_context = _FakeContext("persistent")
        self.cdp_existing_context = _FakeContext("cdp-existing")
        self.cdp_browser = _FakeBrowser("cdp", contexts=[self.cdp_existing_context])

    def launch(self, **kwargs):
        self.calls.append(("launch", kwargs))
        return self.launch_browser

    def launch_persistent_context(self, **kwargs):
        self.calls.append(("launch_persistent_context", kwargs))
        return self.persistent_context

    def connect_over_cdp(self, endpoint, **kwargs):
        self.calls.append(("connect_over_cdp", endpoint))
        return self.cdp_browser


@pytest.fixture
def fake_pw():
    return SimpleNamespace(chromium=_RecordingChromium())


# ── open_browser_context: the three modes ──────────────────────────────────


def test_visible_default_uses_persistent_context_with_profile(
    fake_pw, monkeypatch, tmp_path,
):
    monkeypatch.delenv("JOBPIPE_BROWSER_CDP", raising=False)
    monkeypatch.setenv("JOBPIPE_BROWSER_PROFILE", str(tmp_path / "chrome-profile"))

    from jobpipe.submit.browser.local import open_browser_context

    context, closer = open_browser_context(fake_pw, headless=False)

    kinds = [c[0] for c in fake_pw.chromium.calls]
    assert kinds == ["launch_persistent_context"], kinds
    _, kwargs = fake_pw.chromium.calls[0]
    assert kwargs["user_data_dir"] == str(tmp_path / "chrome-profile")
    assert kwargs["headless"] is False
    # profile dir is created so first run on a fresh machine works
    assert (tmp_path / "chrome-profile").is_dir()
    assert context is fake_pw.chromium.persistent_context

    # closer tears down the persistent context (which owns the window)
    closer()
    assert fake_pw.chromium.persistent_context.closed is True


def test_headless_falls_back_to_launch(fake_pw, monkeypatch):
    monkeypatch.delenv("JOBPIPE_BROWSER_CDP", raising=False)

    from jobpipe.submit.browser.local import open_browser_context

    context, closer = open_browser_context(fake_pw, headless=True)

    kinds = [c[0] for c in fake_pw.chromium.calls]
    assert kinds == ["launch"], kinds
    _, kwargs = fake_pw.chromium.calls[0]
    assert kwargs.get("headless") is True
    # old path: a fresh cookieless context off the launched browser
    assert context.label == "launch:new_context"

    closer()
    assert fake_pw.chromium.launch_browser.closed is True


def test_cdp_attaches_and_reuses_existing_context(fake_pw, monkeypatch):
    monkeypatch.setenv("JOBPIPE_BROWSER_CDP", "http://localhost:9222")

    from jobpipe.submit.browser.local import open_browser_context

    # CDP wins even if headless is requested — it's an explicit opt-in to
    # attach to a user-launched, visible Chrome.
    context, closer = open_browser_context(fake_pw, headless=True)

    kinds = [c[0] for c in fake_pw.chromium.calls]
    assert kinds == ["connect_over_cdp"], kinds
    assert fake_pw.chromium.calls[0][1] == "http://localhost:9222"
    # reuse the already-open context rather than minting a cookieless one
    assert context is fake_pw.chromium.cdp_existing_context

    # closer must NOT close the user's own browser
    closer()
    assert fake_pw.chromium.cdp_browser.closed is False
    assert fake_pw.chromium.cdp_existing_context.closed is False


# ── Integration: UniversalApplicant.apply routes through the helper ─────────


def test_apply_uses_persistent_context(monkeypatch, tmp_path):
    """apply() must create its browser via the persistent-context helper so
    the user's ATS logins persist — not a fresh cookieless Chromium."""
    for k, v in {
        "SUPABASE_URL": "https://example.supabase.co",
        "SUPABASE_KEY": "anon-test",
        "SUPABASE_SERVICE_ROLE_KEY": "service-test",
        "BROWSERBASE_API_KEY": "bb-test",
        "BROWSERBASE_PROJECT_ID": "bb-proj-test",
        "ANTHROPIC_API_KEY": "sk-test",
    }.items():
        monkeypatch.setenv(k, v)
    monkeypatch.delenv("JOBPIPE_BROWSER_CDP", raising=False)
    monkeypatch.setenv("HEADLESS", "0")
    monkeypatch.setenv("JOBPIPE_BROWSER_PROFILE", str(tmp_path / "profile"))

    chromium = _RecordingChromium()

    class _PWcm:
        def __enter__(self):
            return SimpleNamespace(chromium=chromium)

        def __exit__(self, *exc):
            return False

    # Give the fake page enough surface for apply()'s goto + wait calls.
    def _new_page():
        return SimpleNamespace(
            url="https://jobs.example.com/apply",
            goto=lambda *a, **k: None,
            wait_for_load_state=lambda *a, **k: None,
        )

    chromium.persistent_context.new_page = _new_page

    from jobpipe.submit.adapters.prepare_dom import universal as uni

    # ``universal`` binds ``sync_playwright`` at module top via a
    # ``from playwright.sync_api import …`` — patch the bound name directly
    # (robust against sys.modules fakes other tests leave behind).
    monkeypatch.setattr(uni, "sync_playwright", lambda: _PWcm())
    monkeypatch.setattr(
        uni, "resolve_application_url",
        lambda url: {"resolved": url, "is_ats": True, "trail": [], "notes": "ok"},
    )
    monkeypatch.setattr(
        uni, "run_submission_agent",
        lambda **kw: {"success": True, "needs_review": False, "notes": "done"},
    )

    applicant = uni.UniversalApplicant()
    result = applicant.apply(
        {"company": "Example", "application_url": "https://jobs.example.com/apply"},
        resume_path=str(tmp_path / "r.pdf"),
    )

    kinds = [c[0] for c in chromium.calls]
    assert "launch_persistent_context" in kinds, kinds
    assert "launch" not in kinds, "apply must not use the cookieless launch path in visible mode"
    assert result["success"] is True
