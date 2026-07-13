"""Field-fill parity tests for ``prepare_dom.greenhouse`` (Part A / #3).

There was no direct Greenhouse adapter test before the data-driven rewrite
(only Ashby/Lever pinned their overview->form navigation, with empty
form_answers). These tests pin the *field* behaviour the rewrite must
preserve — canonical name-attr fills in build_field_map order, resume upload,
cover-letter paste — plus the new ``required_empty`` key the adapter now
surfaces for the Part B verification pass.

A stub Page (sync, duck-typed) drives the adapter with no real Playwright.
"""

from __future__ import annotations

import pytest

from jobpipe.submit.adapters.prepare_dom.greenhouse import GreenhouseApplicant


class _StubLocator:
    def __init__(self, selector, page, *, visible=False, count=0):
        self.selector = selector
        self.page = page
        self.visible = visible
        self._count = count

    @property
    def first(self):
        return self

    def is_visible(self, timeout: int = 1000) -> bool:
        return self.visible

    def count(self) -> int:
        return self._count

    def nth(self, i: int) -> "_StubLocator":
        return self

    def click(self) -> None:
        pass

    def fill(self, value: str) -> None:
        self.page.fills.append((self.selector, value))
        self.page.values[self.selector] = value

    def set_input_files(self, file_path: str) -> None:
        self.page.uploads.append((self.selector, file_path))

    def input_value(self, timeout: int = 1000) -> str:
        return self.page.values.get(self.selector, "")

    def get_attribute(self, name: str):
        return None


class _StubPage:
    def __init__(self, behaviors: dict):
        # Default "form" -> count=1 (unless a test overrides it) so
        # ``wait_for_form_ready`` (Task 3 / #4) returns immediately instead
        # of really waiting out its poll timeout on every field-fill test
        # here — orthogonal to what's actually being exercised.
        self.behaviors = {"form": {"count": 1}, **behaviors}
        self.fills: list[tuple[str, str]] = []
        self.uploads: list[tuple[str, str]] = []
        self.values: dict[str, str] = {}

    def locator(self, selector: str) -> _StubLocator:
        return _StubLocator(selector, self, **self.behaviors.get(selector, {}))

    def wait_for_load_state(self, *a, **k) -> None:
        return None


@pytest.fixture
def full_form_answers() -> dict:
    return {
        "id": "gh-1",
        "form_answers": {
            "first_name": "Test",
            "last_name": "Applicant",
            "full_name": "Test Applicant",
            "email": "test@example.invalid",
            "phone": "+1-555-0100",
            "linkedin_url": "linkedin.example/in/test",
            "github_url": "github.example/test",
            "current_location": "Atlanta, GA",
            "current_company": "Acme",
            "current_title": "Engineer",
        },
    }


def _patch_screenshot(monkeypatch):
    monkeypatch.setattr(
        GreenhouseApplicant, "take_screenshot",
        lambda self, page, label="form": f"/tmp/{label}.png",
    )


def _all_canonical_visible() -> dict:
    """Make every Greenhouse canonical name selector + phone + resume + cover
    target resolve as fillable."""
    return {
        'input[name="job_application[first_name]"]': {"visible": True},
        'input[name="job_application[last_name]"]': {"visible": True},
        'input[name="job_application[email]"]': {"visible": True},
        'input[type="tel"]:visible': {"visible": True},
        'input[name="job_application[urls][LinkedIn]"]': {"visible": True},
        'input[name="job_application[urls][GitHub]"]': {"visible": True},
        'input[name="job_application[location]"]': {"visible": True},
        'input[name="job_application[company]"]': {"visible": True},
        'input[name="job_application[title]"]': {"visible": True},
        'input[type="file"][name="job_application[resume]"]': {"count": 1},
        # upload-completion evidence (Task 2) — present immediately so the
        # upload confirms on the first poll instead of the default timeout.
        "text=resume.pdf": {"count": 1},
        'textarea[name="job_application[cover_letter]"]': {"visible": True},
    }


def test_fills_canonical_fields_in_build_field_map_order(monkeypatch, full_form_answers, tmp_path):
    _patch_screenshot(monkeypatch)
    resume = tmp_path / "resume.pdf"
    resume.write_bytes(b"%PDF-1.4 fake")
    page = _StubPage(_all_canonical_visible())

    result = GreenhouseApplicant().fill_form(
        page, full_form_answers,
        resume_path=str(resume),
        cover_letter_path="x" * 250,  # long string -> inline cover-letter text
    )

    assert result["success"] is True
    filled = result["fields_filled"]
    # Canonical identity fields all reported filled, in build_field_map order.
    for label in ("First Name", "Last Name", "Email", "Phone"):
        assert label in filled
    assert filled.index("First Name") < filled.index("Last Name") < filled.index("Email") < filled.index("Phone")
    # Resume uploaded to the canonical file input; cover letter pasted.
    assert ('input[type="file"][name="job_application[resume]"]', str(resume)) in page.uploads
    assert "Uploaded resume" in result["notes"]
    assert "Pasted cover letter" in result["notes"]


def test_returns_required_empty_key(monkeypatch, full_form_answers, tmp_path):
    """The rewrite surfaces ``required_empty`` so the Part B verification pass
    can render 'still needs: ...'. With every required field present it is
    an empty list."""
    _patch_screenshot(monkeypatch)
    resume = tmp_path / "resume.pdf"
    resume.write_bytes(b"%PDF-1.4 fake")
    page = _StubPage(_all_canonical_visible())

    result = GreenhouseApplicant().fill_form(
        page, full_form_answers,
        resume_path=str(resume), cover_letter_path="x" * 250,
    )

    assert "required_empty" in result
    assert result["required_empty"] == []


def test_required_empty_lists_field_with_no_value(monkeypatch, tmp_path):
    """A required field whose value is missing from form_answers shows up in
    required_empty (Phone here)."""
    _patch_screenshot(monkeypatch)
    resume = tmp_path / "resume.pdf"
    resume.write_bytes(b"%PDF-1.4 fake")
    job = {
        "id": "gh-2",
        "form_answers": {
            "first_name": "Test", "last_name": "Applicant",
            "email": "test@example.invalid",
            # no phone
        },
    }
    page = _StubPage(_all_canonical_visible())

    result = GreenhouseApplicant().fill_form(
        page, job, resume_path=str(resume), cover_letter_path=None,
    )

    assert "Phone" in result["required_empty"]


# ── Tolerant readiness wait wiring (Task 3 / #4) ────────────────────────────

def test_greenhouse_surfaces_readiness_timeout_when_form_never_appears(
    monkeypatch, tmp_path,
):
    import jobpipe.submit.adapters.prepare_dom.greenhouse as gh_module

    _patch_screenshot(monkeypatch)
    monkeypatch.setattr(
        gh_module, "wait_for_form_ready",
        lambda page, log=None: {"ready": False, "signal": None},
    )
    resume = tmp_path / "resume.pdf"
    resume.write_bytes(b"%PDF-1.4 fake")
    page = _StubPage({})
    job = {"id": "gh-timeout", "form_answers": {}}

    result = GreenhouseApplicant().fill_form(page, job, resume_path=str(resume))

    assert result["readiness_timeout"] is True


def test_greenhouse_readiness_timeout_false_when_form_appears(monkeypatch, tmp_path):
    _patch_screenshot(monkeypatch)
    resume = tmp_path / "resume.pdf"
    resume.write_bytes(b"%PDF-1.4 fake")
    page = _StubPage({})  # default "form": count=1 fast-path
    job = {"id": "gh-ready", "form_answers": {}}

    result = GreenhouseApplicant().fill_form(page, job, resume_path=str(resume))

    assert result["readiness_timeout"] is False
