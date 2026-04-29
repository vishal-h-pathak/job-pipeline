"""Direct unit tests for ``jobpipe.submit.adapters.prepare_dom._common``.

PR-7 introduces a shared module of sync Playwright helpers for the
prepare-only adapters. These tests exercise every helper against a stub
``Page`` object so the suite can run with no Playwright install — the helpers
are deliberately duck-typed (only Page methods like ``locator``, ``first``,
``is_visible``, ``click``, ``fill``, ``count``, ``set_input_files`` are used).
"""

from __future__ import annotations

from typing import Optional

import pytest

from jobpipe.submit.adapters.prepare_dom._common import (
    build_field_map,
    fill_text,
    label_selectors,
    load_cover_letter,
    name_attr_selectors,
    note_unfilled_custom_questions,
    paste_textarea,
    upload_file,
)


# ── Stub Page / Locator infrastructure ─────────────────────────────────────

class _StubLocator:
    def __init__(
        self,
        selector: str,
        page: "_StubPage",
        *,
        visible: bool = False,
        count: int = 0,
        raise_on_visible: bool = False,
    ):
        self.selector = selector
        self.page = page
        self.visible = visible
        self._count = count
        self.raise_on_visible = raise_on_visible
        self.clicked = False
        self.filled_with: Optional[str] = None
        self.uploaded: Optional[str] = None

    @property
    def first(self):
        return self

    def is_visible(self, timeout: int = 1000) -> bool:
        if self.raise_on_visible:
            raise RuntimeError(f"stub: is_visible blew up for {self.selector}")
        return self.visible

    def count(self) -> int:
        return self._count

    def click(self) -> None:
        self.clicked = True

    def fill(self, value: str) -> None:
        self.filled_with = value
        self.page.fills.append((self.selector, value))

    def set_input_files(self, file_path: str) -> None:
        self.uploaded = file_path
        self.page.uploads.append((self.selector, file_path))


class _StubPage:
    """A page where each selector resolves to a pre-configured locator.

    Tests construct one with a dict of ``{selector: locator_kwargs}`` and the
    page returns matching locators. Unknown selectors return a locator that
    is invisible / count==0 so the helper falls through to the next."""

    def __init__(self, behaviors: dict):
        self.behaviors = behaviors
        self.fills: list[tuple[str, str]] = []
        self.uploads: list[tuple[str, str]] = []
        self.locator_calls: list[str] = []

    def locator(self, selector: str) -> _StubLocator:
        self.locator_calls.append(selector)
        kwargs = self.behaviors.get(selector, {})
        return _StubLocator(selector, self, **kwargs)


# ── label_selectors / name_attr_selectors ──────────────────────────────────

def test_label_selectors_returns_four_in_canonical_order():
    out = label_selectors("First Name")
    assert out == [
        'label:has-text("First Name") input',
        'label:has-text("First Name") >> input',
        'input[aria-label="First Name"]',
        'input[placeholder*="First Name" i]',
    ]


def test_name_attr_selectors_present_returns_input_then_textarea():
    name_map = {"Email": "job_application[email]"}
    assert name_attr_selectors(name_map, "Email") == [
        'input[name="job_application[email]"]',
        'textarea[name="job_application[email]"]',
    ]


def test_name_attr_selectors_missing_returns_empty_list():
    assert name_attr_selectors({"Other": "x"}, "Email") == []


# ── fill_text ──────────────────────────────────────────────────────────────

def test_fill_text_clicks_and_fills_first_visible_match():
    page = _StubPage({"sel-a": {"visible": True}})
    ok = fill_text(page, ["sel-a", "sel-b"], "value-1")
    assert ok is True
    assert page.fills == [("sel-a", "value-1")]
    # second selector never queried
    assert page.locator_calls == ["sel-a"]


def test_fill_text_falls_through_when_first_selector_not_visible():
    page = _StubPage({
        "sel-a": {"visible": False},
        "sel-b": {"visible": True},
    })
    ok = fill_text(page, ["sel-a", "sel-b"], "value-2")
    assert ok is True
    assert page.fills == [("sel-b", "value-2")]
    assert page.locator_calls == ["sel-a", "sel-b"]


def test_fill_text_swallows_per_selector_exceptions_and_continues():
    page = _StubPage({
        "sel-a": {"raise_on_visible": True},
        "sel-b": {"visible": True},
    })
    ok = fill_text(page, ["sel-a", "sel-b"], "value-3")
    assert ok is True
    assert page.fills == [("sel-b", "value-3")]


def test_fill_text_returns_false_when_no_selector_matches():
    page = _StubPage({"sel-a": {"visible": False}, "sel-b": {"visible": False}})
    ok = fill_text(page, ["sel-a", "sel-b"], "value-4")
    assert ok is False
    assert page.fills == []


# ── Phone selector ordering (intl-tel-input coverage) ──────────────────────

def test_phone_selectors_pick_visible_tel_before_label_chain():
    """intl-tel-input wraps a real ``<input type="tel">`` inside a parent
    that also contains a hidden country-search ``<input>``. The generic
    ``label_selectors`` chain can match the hidden search input first via
    DOM order, leaving the real tel field empty (the silent-miss observed
    on the Anthropic Fellows form). Each per-ATS phone selector list
    leads with ``input[type="tel"]:visible`` so the tel input wins
    before the label fallback is even consulted.

    Stub: both the tel-visible selector AND the label-input selector are
    visible. If the ordering didn't matter, the label selector could
    have won. The assertion is that only the tel selector got filled
    and the label fallback was never reached.
    """
    from jobpipe.submit.adapters.prepare_dom.greenhouse import (
        _GREENHOUSE_PHONE_SELECTORS,
    )

    page = _StubPage({
        # The phone-specific lead selector — represents the visible
        # <input type="tel"> that intl-tel-input keeps in the DOM.
        'input[type="tel"]:visible': {"visible": True},
        # A generic-label fallback that, if reached, would match the
        # WRONG element (the hidden iti country-search input the
        # library injects). Marked visible to prove the ordering — not
        # because iti-search is actually visible in production.
        'label:has-text("Phone") input': {"visible": True},
    })

    full_chain = _GREENHOUSE_PHONE_SELECTORS + label_selectors("Phone")
    ok = fill_text(page, full_chain, "+1-555-0100")

    assert ok is True
    assert page.fills == [('input[type="tel"]:visible', "+1-555-0100")]
    # Walk-through stops at the first visible match — label fallback
    # never gets queried.
    assert 'label:has-text("Phone") input' not in page.locator_calls


def test_phone_selectors_canonical_order_per_ats():
    """The three per-ATS phone selector chains all lead with
    ``input[type="tel"]:visible`` (the intl-tel-input anchor) so the
    fix has uniform shape across Greenhouse, Lever, and Ashby. Per-ATS
    fallbacks differ (Greenhouse pins ``name="job_application[phone]"``,
    Lever pins ``name="phone"``, Ashby has no canonical name and skips
    that step) — verify the leading selector is identical.
    """
    from jobpipe.submit.adapters.prepare_dom.greenhouse import (
        _GREENHOUSE_PHONE_SELECTORS,
    )
    from jobpipe.submit.adapters.prepare_dom.lever import (
        _LEVER_PHONE_SELECTORS,
    )
    from jobpipe.submit.adapters.prepare_dom.ashby import (
        _ASHBY_PHONE_SELECTORS,
    )

    for selectors in (
        _GREENHOUSE_PHONE_SELECTORS,
        _LEVER_PHONE_SELECTORS,
        _ASHBY_PHONE_SELECTORS,
    ):
        assert selectors[0] == 'input[type="tel"]:visible'
        assert 'input[id="phone"]' in selectors
        assert 'input[aria-label="Phone"]' in selectors


# ── upload_file ────────────────────────────────────────────────────────────

def test_upload_file_uses_first_selector_with_count_gt_zero():
    page = _StubPage({
        "sel-a": {"count": 0},
        "sel-b": {"count": 1},
    })
    ok = upload_file(page, ["sel-a", "sel-b"], "/tmp/resume.pdf")
    assert ok is True
    assert page.uploads == [("sel-b", "/tmp/resume.pdf")]


def test_upload_file_returns_false_when_no_selector_finds_input():
    page = _StubPage({"sel-a": {"count": 0}})
    ok = upload_file(page, ["sel-a"], "/tmp/resume.pdf")
    assert ok is False
    assert page.uploads == []


def test_upload_file_swallows_per_selector_exceptions():
    page = _StubPage({
        "sel-a": {"raise_on_visible": True, "count": 0},
        "sel-b": {"count": 1},
    })
    ok = upload_file(page, ["sel-a", "sel-b"], "/tmp/r.pdf")
    assert ok is True


# ── paste_textarea ─────────────────────────────────────────────────────────

def test_paste_textarea_uses_first_visible_textarea():
    page = _StubPage({
        "textarea[name=cover]": {"visible": False},
        "textarea": {"visible": True},
    })
    ok = paste_textarea(
        page, ["textarea[name=cover]", "textarea"], "cover body"
    )
    assert ok is True
    assert page.fills == [("textarea", "cover body")]


def test_paste_textarea_returns_false_when_no_visible_textarea():
    page = _StubPage({"textarea": {"visible": False}})
    ok = paste_textarea(page, ["textarea"], "ignored")
    assert ok is False


# ── load_cover_letter ──────────────────────────────────────────────────────

def test_load_cover_letter_reads_existing_file(tmp_path):
    p = tmp_path / "cover.txt"
    body = "Dear Hiring Manager,\n\nI am writing to apply for the role.\n"
    p.write_text(body, encoding="utf-8")
    assert load_cover_letter(str(p)) == body


def test_load_cover_letter_long_string_returned_as_inline_text():
    long_text = "x" * 250
    assert load_cover_letter(long_text) == long_text


def test_load_cover_letter_pathmax_oserror_returns_inline_text():
    """Strings longer than the OS PATH_MAX (~1024 on macOS) trip ``os.stat()``
    inside ``Path.exists()`` with ``OSError [Errno 63] File name too long``.
    The helper must catch that and fall through to the inline-text branch
    rather than propagating the error to the per-ATS adapter (which used to
    crash mid-fill on real cover letters around 2 000 chars long — the
    Anthropic Fellows pre-fill failure mode that motivated this test).
    """
    body = (
        "Dear Hiring Manager,\n\n"
        + ("This is a 2000-character cover-letter body. " * 50)
    )
    assert len(body) > 1024, "test premise: body must exceed macOS PATH_MAX"
    # Must not raise; must return the body unchanged.
    assert load_cover_letter(body) == body


def test_load_cover_letter_short_string_with_no_file_returns_empty():
    assert load_cover_letter("not_a_path.txt") == ""


def test_load_cover_letter_empty_or_none_returns_empty_string():
    assert load_cover_letter("") == ""
    assert load_cover_letter(None) == ""


# ── build_field_map ────────────────────────────────────────────────────────

def test_build_field_map_maps_known_form_answers_keys():
    job = {"form_answers": {
        "first_name": "Test",
        "last_name": "Applicant",
        "full_name": "Test Applicant",
        "email": "test@example.invalid",
        "phone": "+1-555-0100",
        "linkedin_url": "linkedin.example/in/test",
        "github_url": "github.example/test",
        "portfolio_url": "test.example",
        "current_location": "Atlanta, GA",
        "current_company": "Acme",
        "current_title": "Engineer",
    }}
    fm = build_field_map(job)
    assert fm["First Name"] == "Test"
    assert fm["Last Name"] == "Applicant"
    assert fm["Full Name"] == "Test Applicant"
    # "Name" alias points at full_name
    assert fm["Name"] == "Test Applicant"
    assert fm["Email"] == "test@example.invalid"
    assert fm["Phone"] == "+1-555-0100"
    # social URL aliases
    assert fm["LinkedIn"] == fm["LinkedIn URL"] == "linkedin.example/in/test"
    assert fm["GitHub"] == fm["GitHub URL"] == "github.example/test"
    assert fm["Website"] == fm["Portfolio"] == "test.example"
    # location aliases
    assert fm["Location"] == fm["Current Location"] == fm["City"] == "Atlanta, GA"
    assert fm["Current Company"] == fm["Company"] == "Acme"
    assert fm["Current Title"] == fm["Title"] == "Engineer"


def test_build_field_map_missing_keys_default_to_empty_string():
    fm = build_field_map({"form_answers": {}})
    for v in fm.values():
        assert v == ""


def test_build_field_map_missing_form_answers_block_is_empty():
    fm = build_field_map({})
    for v in fm.values():
        assert v == ""


# ── note_unfilled_custom_questions ─────────────────────────────────────────

def test_note_unfilled_custom_questions_appends_when_questions_present():
    notes: list[str] = []
    job = {"form_answers": {"additional_questions": [{"q": "A"}, {"q": "B"}]}}
    note_unfilled_custom_questions(job, notes)
    assert notes == [
        "2 role-specific question(s) NOT auto-filled - paste from cockpit drafts"
    ]


def test_note_unfilled_custom_questions_noop_when_empty():
    notes: list[str] = []
    note_unfilled_custom_questions({"form_answers": {"additional_questions": []}}, notes)
    note_unfilled_custom_questions({"form_answers": {}}, notes)
    note_unfilled_custom_questions({}, notes)
    assert notes == []
