"""Direct unit tests for ``jobpipe.submit.adapters.prepare_dom.ashby``.

PR-22 introduces a leading URL-normalisation hop in
``AshbyApplicant.fill_form``: if ``page.url`` is the Ashby overview page
(``jobs.ashbyhq.com/{org}/{job_id}``) the adapter navigates to
``.../application`` before surveying. These tests pin that behavior:

  - overview URL: assert ``page.goto`` is called with ``/application`` appended
  - already-on-application URL: idempotent — no extra goto
  - URL with query string: the query string is preserved through the goto

The stub ``_StubPage`` is local to this file (rather than imported from
``conftest._FakePage``) because the conftest fake is async-shaped, while
the prepare_dom adapters use sync Playwright. The pattern mirrors
``tests/test_prepare_dom_common.py``.
"""

from __future__ import annotations

import pytest

from jobpipe.submit.adapters.prepare_dom.ashby import AshbyApplicant


# ── Stub Page / Locator infrastructure ─────────────────────────────────────

class _StubLocator:
    """Always invisible — fill_text/upload_file/paste_textarea fall through
    on every selector. The PR-22 tests only care about pre-fill navigation;
    they don't exercise field filling."""

    def __init__(self) -> None:
        self.first = self

    def is_visible(self, timeout: int = 1000) -> bool:
        return False

    def count(self) -> int:
        return 0

    def click(self) -> None:  # pragma: no cover - never reached
        pass

    def fill(self, value: str) -> None:  # pragma: no cover
        pass

    def set_input_files(self, file_path: str) -> None:  # pragma: no cover
        pass


class _StubPage:
    """Sync Playwright Page stand-in. Records ``goto`` calls and the
    ``wait_until`` / ``timeout`` kwargs so the test can assert exactly what
    the adapter requested. ``locator`` always returns an invisible locator
    so fill_form falls through every selector and exits with success=False
    (which is fine — the assertion target is the goto call, not fill state).
    """

    def __init__(self, url: str) -> None:
        self.url = url
        self.goto_calls: list[tuple[str, dict]] = []
        self.screenshot_calls: list[dict] = []

    def goto(self, target: str, **kwargs) -> None:
        self.goto_calls.append((target, kwargs))
        # Reflect the navigation so ``page.url`` reads what an integration
        # test would see post-goto.
        self.url = target

    def wait_for_load_state(self, *args, **kwargs) -> None:
        return None

    def locator(self, selector: str) -> _StubLocator:
        return _StubLocator()

    def screenshot(self, **kwargs):  # pragma: no cover - never reached
        self.screenshot_calls.append(kwargs)
        return b""


@pytest.fixture
def stub_job() -> dict:
    """Minimal job dict — empty form_answers so build_field_map yields no
    non-empty values, and fill_text loops a no-op. ``id`` is read by the
    take_screenshot label."""
    return {"id": "test-ashby", "form_answers": {}}


def _patch_screenshot(monkeypatch: pytest.MonkeyPatch) -> None:
    """``BaseApplicant.take_screenshot`` writes to ``OUTPUT_DIR``; stub it
    to a string so tests don't depend on disk I/O or the tailor paths
    module."""
    monkeypatch.setattr(
        AshbyApplicant,
        "take_screenshot",
        lambda self, page, label="form": f"/tmp/{label}.png",
    )


# ── PR-22: overview → /application navigation ──────────────────────────────

def test_navigates_to_application_when_on_overview(monkeypatch, stub_job):
    """Adapter starts on the Ashby overview URL — should goto the form URL
    with ``/application`` appended before surveying."""
    _patch_screenshot(monkeypatch)
    page = _StubPage("https://jobs.ashbyhq.com/far.ai/abc123")
    applicant = AshbyApplicant()

    applicant.fill_form(page, stub_job)

    assert len(page.goto_calls) == 1
    target, kwargs = page.goto_calls[0]
    assert target == "https://jobs.ashbyhq.com/far.ai/abc123/application"
    assert kwargs.get("wait_until") == "domcontentloaded"
    assert kwargs.get("timeout") == 45000


def test_idempotent_when_already_on_application_form(monkeypatch, stub_job):
    """If the orchestrator hands us a URL that already ends in
    ``/application``, the adapter must not navigate again — re-navigating
    would needlessly reset the form state and risk losing in-progress
    fills on retries."""
    _patch_screenshot(monkeypatch)
    page = _StubPage(
        "https://jobs.ashbyhq.com/far.ai/abc123/application"
    )
    applicant = AshbyApplicant()

    applicant.fill_form(page, stub_job)

    assert page.goto_calls == []


def test_idempotent_when_application_url_has_trailing_slash(
    monkeypatch, stub_job
):
    """Trailing slash on ``/application/`` still counts as the form URL —
    the path-strip in the adapter should treat both the same."""
    _patch_screenshot(monkeypatch)
    page = _StubPage(
        "https://jobs.ashbyhq.com/far.ai/abc123/application/"
    )
    applicant = AshbyApplicant()

    applicant.fill_form(page, stub_job)

    assert page.goto_calls == []


def test_preserves_query_string(monkeypatch, stub_job):
    """Embedded Ashby boards pass ``?embed=js`` (and similar). The goto
    target must keep the query string intact so the form renders in the
    correct embed mode."""
    _patch_screenshot(monkeypatch)
    page = _StubPage(
        "https://jobs.ashbyhq.com/far.ai/abc123?embed=js"
    )
    applicant = AshbyApplicant()

    applicant.fill_form(page, stub_job)

    assert len(page.goto_calls) == 1
    target, _ = page.goto_calls[0]
    # Path got /application appended; query string survived.
    assert (
        target
        == "https://jobs.ashbyhq.com/far.ai/abc123/application?embed=js"
    )
