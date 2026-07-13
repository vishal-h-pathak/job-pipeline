"""tests/test_page_truth.py — Path-A browser-truth probes (P0 #1).

``jobpipe.submit.page_truth`` ports the success-signal needles from the
retired Browserbase Path B (``confirm.py``) into the live sync-Playwright
pre-fill path, and adds a generic validation-error DOM scan. These tests
exercise every probe against a stub sync Page — no real Playwright, no
Stagehand, no network.
"""

from __future__ import annotations

from jobpipe.submit.page_truth import (
    capture_truth,
    probe_error_signals,
    probe_success_signal,
)


# ── Stub Page / Locator infrastructure ─────────────────────────────────────

class _StubEl:
    def __init__(self, *, visible: bool = True, text: str = ""):
        self._visible = visible
        self._text = text

    def is_visible(self, timeout: int = 500) -> bool:
        return self._visible

    def text_content(self) -> str:
        return self._text


class _StubLocator:
    def __init__(self, els: list[_StubEl] | None = None, *, raises: bool = False):
        self._els = els or []
        self._raises = raises

    def count(self) -> int:
        if self._raises:
            raise RuntimeError("boom")
        return len(self._els)

    def nth(self, i: int) -> _StubEl:
        return self._els[i]


class _StubPage:
    def __init__(self, *, url: str = "", content: str = "",
                 error_selectors: dict[str, list[_StubEl]] | None = None):
        self.url = url
        self._content = content
        self._error_selectors = error_selectors or {}

    def content(self) -> str:
        return self._content

    def locator(self, selector: str) -> _StubLocator:
        if selector not in self._error_selectors:
            return _StubLocator([])
        return _StubLocator(self._error_selectors[selector])


# ── probe_success_signal ────────────────────────────────────────────────────

def test_probe_success_signal_url_redirect_match():
    page = _StubPage(url="https://boards.greenhouse.io/acme/applications/thank_you")
    sig = probe_success_signal(page, "greenhouse")
    assert sig == {"kind": "url_redirect", "detail": page.url}


def test_probe_success_signal_page_text_match():
    page = _StubPage(
        url="https://boards.greenhouse.io/acme/jobs/1",
        content="<div>Thanks for applying to Acme!</div>",
    )
    sig = probe_success_signal(page, "greenhouse")
    assert sig == {"kind": "page_text", "detail": "Thanks for applying"}


def test_probe_success_signal_lever_and_ashby_needles():
    lever_page = _StubPage(url="https://jobs.lever.co/acme/x/thanks")
    assert probe_success_signal(lever_page, "lever")["kind"] == "url_redirect"

    ashby_page = _StubPage(content="Your application has been submitted")
    assert probe_success_signal(ashby_page, "ashby")["kind"] == "page_text"


def test_probe_success_signal_returns_none_when_no_needle_matches():
    page = _StubPage(url="https://boards.greenhouse.io/acme/jobs/1", content="<form></form>")
    assert probe_success_signal(page, "greenhouse") is None


def test_probe_success_signal_unknown_ats_returns_none():
    page = _StubPage(url="https://example.invalid/thank_you")
    assert probe_success_signal(page, "workday") is None


def test_probe_success_signal_swallows_url_read_exception():
    class _BoomPage:
        content = lambda self: ""  # noqa: E731

        @property
        def url(self):
            raise RuntimeError("boom")

    assert probe_success_signal(_BoomPage(), "greenhouse") is None


# ── probe_error_signals ──────────────────────────────────────────────────────

def test_probe_error_signals_collects_visible_alert_text():
    page = _StubPage(error_selectors={
        '[role="alert"]': [_StubEl(visible=True, text="Phone is required")],
    })
    assert probe_error_signals(page) == ["Phone is required"]


def test_probe_error_signals_skips_invisible_elements():
    page = _StubPage(error_selectors={
        ".error": [_StubEl(visible=False, text="hidden error")],
    })
    assert probe_error_signals(page) == []


def test_probe_error_signals_dedupes_across_selectors():
    page = _StubPage(error_selectors={
        '[role="alert"]': [_StubEl(visible=True, text="Email is invalid")],
        '[aria-invalid="true"]': [_StubEl(visible=True, text="Email is invalid")],
    })
    assert probe_error_signals(page) == ["Email is invalid"]


def test_probe_error_signals_swallows_selector_exception():
    page = _StubPage()
    page._error_selectors = {".error": []}
    # Force count() to raise for one selector — must not propagate.
    broken = _StubLocator([], raises=True)
    page.locator = lambda sel: broken if sel == ".field-error" else _StubLocator([])
    assert probe_error_signals(page) == []


def test_probe_error_signals_no_errors_present():
    page = _StubPage()
    assert probe_error_signals(page) == []


# ── capture_truth ────────────────────────────────────────────────────────────

def test_capture_truth_bundles_url_signal_and_errors():
    page = _StubPage(
        url="https://boards.greenhouse.io/acme/jobs/1",
        content="<form></form>",
        error_selectors={'[role="alert"]': [_StubEl(visible=True, text="Phone is required")]},
    )
    truth = capture_truth(page, "greenhouse")
    assert truth["final_url"] == page.url
    assert truth["success_signal"] is None
    assert truth["error_signals"] == ["Phone is required"]


def test_capture_truth_success_case_has_no_error_signals_required():
    page = _StubPage(url="https://boards.greenhouse.io/acme/applications/thank_you")
    truth = capture_truth(page, "greenhouse")
    assert truth["success_signal"]["kind"] == "url_redirect"
    assert truth["error_signals"] == []
