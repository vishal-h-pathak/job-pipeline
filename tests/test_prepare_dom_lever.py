"""Direct unit tests for ``jobpipe.submit.adapters.prepare_dom.lever``.

PR-22 introduces a leading URL-normalisation hop in
``LeverApplicant.fill_form``: if ``page.url`` is the Lever overview page
(``jobs.lever.co/{org}/{job_id}``) the adapter navigates to ``.../apply``
before surveying. Note: ``/apply`` here, not ``/application`` — Lever's
URL convention differs from Ashby's.

These tests pin that behavior with the same shape as
``tests/test_prepare_dom_ashby.py`` so a future reader can compare the
two adapters side-by-side.
"""

from __future__ import annotations

import pytest

from jobpipe.submit.adapters.prepare_dom.lever import LeverApplicant


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
    """Sync Playwright Page stand-in. Records ``goto`` calls so the test
    can assert exactly what the adapter requested."""

    def __init__(self, url: str) -> None:
        self.url = url
        self.goto_calls: list[tuple[str, dict]] = []

    def goto(self, target: str, **kwargs) -> None:
        self.goto_calls.append((target, kwargs))
        self.url = target

    def wait_for_load_state(self, *args, **kwargs) -> None:
        return None

    def locator(self, selector: str) -> _StubLocator:
        return _StubLocator()


@pytest.fixture
def stub_job() -> dict:
    return {"id": "test-lever", "form_answers": {}}


def _patch_screenshot(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        LeverApplicant,
        "take_screenshot",
        lambda self, page, label="form": f"/tmp/{label}.png",
    )


# ── PR-22: overview → /apply navigation ────────────────────────────────────

def test_navigates_to_apply_when_on_overview(monkeypatch, stub_job):
    """Adapter starts on the Lever overview URL — should goto the form URL
    with ``/apply`` appended before surveying."""
    _patch_screenshot(monkeypatch)
    page = _StubPage(
        "https://jobs.lever.co/epoch-ai/de7b4c71-ece2-454a-be70-e7b75c5f3b23"
    )
    applicant = LeverApplicant()

    applicant.fill_form(page, stub_job)

    assert len(page.goto_calls) == 1
    target, kwargs = page.goto_calls[0]
    assert target == (
        "https://jobs.lever.co/epoch-ai/"
        "de7b4c71-ece2-454a-be70-e7b75c5f3b23/apply"
    )
    assert kwargs.get("wait_until") == "domcontentloaded"
    assert kwargs.get("timeout") == 45000


def test_idempotent_when_already_on_apply_form(monkeypatch, stub_job):
    """URL already ends in ``/apply`` — no extra goto."""
    _patch_screenshot(monkeypatch)
    page = _StubPage("https://jobs.lever.co/epoch-ai/abc/apply")
    applicant = LeverApplicant()

    applicant.fill_form(page, stub_job)

    assert page.goto_calls == []


def test_idempotent_when_apply_url_has_trailing_slash(monkeypatch, stub_job):
    """Trailing slash on ``/apply/`` still counts as the form URL."""
    _patch_screenshot(monkeypatch)
    page = _StubPage("https://jobs.lever.co/epoch-ai/abc/apply/")
    applicant = LeverApplicant()

    applicant.fill_form(page, stub_job)

    assert page.goto_calls == []


def test_preserves_query_string(monkeypatch, stub_job):
    """Lever embeds and tracking params (``?lever-source=...``) must survive
    the goto so attribution works after the human submits."""
    _patch_screenshot(monkeypatch)
    page = _StubPage(
        "https://jobs.lever.co/epoch-ai/abc?lever-source=referral"
    )
    applicant = LeverApplicant()

    applicant.fill_form(page, stub_job)

    assert len(page.goto_calls) == 1
    target, _ = page.goto_calls[0]
    assert target == (
        "https://jobs.lever.co/epoch-ai/abc/apply?lever-source=referral"
    )


def test_eu_subdomain_navigates_to_apply(monkeypatch, stub_job):
    """The EU subdomain (``jobs.eu.lever.co``) follows the same URL
    pattern; navigation logic must work identically to the US host."""
    _patch_screenshot(monkeypatch)
    page = _StubPage("https://jobs.eu.lever.co/some-org/job-id")
    applicant = LeverApplicant()

    applicant.fill_form(page, stub_job)

    assert len(page.goto_calls) == 1
    target, _ = page.goto_calls[0]
    assert target == "https://jobs.eu.lever.co/some-org/job-id/apply"


# ── Part A: data-driven field-fill parity ──────────────────────────────────

class _FillLocator:
    def __init__(self, selector, page, *, visible=False, count=0):
        self.selector, self.page = selector, page
        self.visible, self._count = visible, count

    @property
    def first(self):
        return self

    def is_visible(self, timeout: int = 1000) -> bool:
        return self.visible

    def count(self) -> int:
        return self._count

    def nth(self, i: int) -> "_FillLocator":
        return self

    def click(self) -> None:
        pass

    def fill(self, value: str) -> None:
        self.page.fills.append((self.selector, value))
        self.page.values[self.selector] = value

    def set_input_files(self, file_path: str) -> None:
        self.page.uploads.append((self.selector, file_path))

    def input_value(self) -> str:
        return self.page.values.get(self.selector, "")

    def get_attribute(self, name: str):
        return None


class _FillPage:
    def __init__(self, behaviors, url="https://jobs.lever.co/acme/123/apply"):
        self.behaviors, self.url = behaviors, url
        self.fills: list = []
        self.uploads: list = []
        self.goto_calls: list = []
        self.values: dict = {}

    def goto(self, target, **kw):
        self.goto_calls.append((target, kw))
        self.url = target

    def wait_for_load_state(self, *a, **k):
        return None

    def locator(self, selector):
        return _FillLocator(selector, self, **self.behaviors.get(selector, {}))


def test_lever_fills_single_name_field_with_full_name(monkeypatch, tmp_path):
    """Lever puts the full name in name="name" — the adapter overrides the
    Name / Full Name values to the computed full name and the data-driven
    fill lands it in that single field."""
    _patch_screenshot(monkeypatch)
    resume = tmp_path / "r.pdf"
    resume.write_bytes(b"%PDF fake")
    page = _FillPage({
        'input[name="name"]': {"visible": True},
        'input[name="email"]': {"visible": True},
        'input[type="tel"]:visible': {"visible": True},
        'input[type="file"][name="resume"]': {"count": 1},
        'textarea[name="comments"]': {"visible": True},
    })
    job = {"id": "lev-1", "form_answers": {
        "first_name": "Test", "last_name": "Applicant",
        "full_name": "Test Applicant", "email": "t@e.invalid",
        "phone": "+1-555-0100",
    }}

    result = LeverApplicant().fill_form(
        page, job, resume_path=str(resume), cover_letter_path="x" * 250,
    )

    assert result["success"] is True
    # name="name" got the FULL name (last write wins across First/Full/Name).
    assert ('input[name="name"]', "Test Applicant") in page.fills
    assert "Name" in result["fields_filled"]
    assert result["required_empty"] == []
    assert "Uploaded resume" in result["notes"]
    assert "Pasted cover letter" in result["notes"]


def test_lever_required_empty_when_name_field_missing(monkeypatch, tmp_path):
    _patch_screenshot(monkeypatch)
    page = _FillPage({'input[name="email"]': {"visible": True}})
    job = {"id": "lev-2", "form_answers": {
        "full_name": "Test Applicant", "email": "t@e.invalid",
    }}

    result = LeverApplicant().fill_form(page, job)

    # No visible name field / phone / resume -> reported in required_empty.
    assert "Name" in result["required_empty"]
    assert "Phone" in result["required_empty"]
