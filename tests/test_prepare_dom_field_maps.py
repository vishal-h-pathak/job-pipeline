"""Tests for the declarative field-map layer (Part A).

``jobpipe.submit.adapters.prepare_dom.field_maps`` introduces:

  - ``load_field_map(ats)`` — read the per-ATS field specs from
    ``field_maps.yml`` (cached, graceful on missing ATS).
  - ``apply_field_map(page, field_specs, values)`` — one generic filler
    that dispatches each spec to the right ``_common`` primitive
    (``fill_text`` / ``upload_file`` / ``paste_textarea`` / ``select_option``)
    and reports which *required* fields ended up empty.

These exercise the generic filler against a stub Page so the suite runs
with no real Playwright. The stub mirrors the duck-typed contract the
``_common`` primitives expect (``locator().first`` → element with
``is_visible`` / ``count`` / ``click`` / ``fill`` / ``set_input_files`` /
``select_option``).
"""

from __future__ import annotations

from typing import Optional

from jobpipe.submit.adapters.prepare_dom.field_maps import (
    apply_field_map,
    load_field_map,
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
    ):
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

    def click(self) -> None:
        pass

    def fill(self, value: str) -> None:
        self.page.fills.append((self.selector, value))

    def set_input_files(self, file_path: str) -> None:
        self.page.uploads.append((self.selector, file_path))

    def select_option(self, value: str) -> None:
        self.page.selects.append((self.selector, value))


class _StubPage:
    """Each selector resolves to a pre-configured locator. Unknown selectors
    resolve to an invisible / count==0 locator so the helper falls through."""

    def __init__(self, behaviors: Optional[dict] = None):
        self.behaviors = behaviors or {}
        self.fills: list[tuple[str, str]] = []
        self.uploads: list[tuple[str, str]] = []
        self.selects: list[tuple[str, str]] = []
        self.locator_calls: list[str] = []

    def locator(self, selector: str) -> _StubLocator:
        self.locator_calls.append(selector)
        return _StubLocator(selector, self, **self.behaviors.get(selector, {}))


# ── load_field_map ─────────────────────────────────────────────────────────

def test_load_field_map_greenhouse_returns_ordered_specs():
    specs = load_field_map("greenhouse")
    assert isinstance(specs, list) and specs
    # First spec is First Name keyed on the canonical Greenhouse name attr.
    assert specs[0]["key"] == "First Name"
    assert specs[0]["name"] == "job_application[first_name]"
    # Resume (file) and cover letter (textarea) specs are present.
    types = {s.get("type", "text") for s in specs}
    assert "file" in types
    assert "textarea" in types


def test_load_field_map_lever_and_ashby_present():
    for ats in ("lever", "ashby"):
        specs = load_field_map(ats)
        assert isinstance(specs, list) and specs


def test_load_field_map_unknown_ats_returns_empty_list():
    assert load_field_map("does-not-exist") == []


def test_load_field_map_ashby_applies_fuzzy_default_to_text_fields():
    """Ashby's per-ATS ``defaults: {fuzzy_name_fallback: true}`` must be
    merged into every text spec so the loader hands apply_field_map fields
    that carry the fuzzy flag (Ashby has no name map and relies on the
    ``input[name*=...]`` fuzzy fallback)."""
    specs = load_field_map("ashby")
    text_specs = [s for s in specs if s.get("type", "text") == "text"]
    assert text_specs
    assert all(s.get("fuzzy_name_fallback") for s in text_specs)


# ── apply_field_map: dispatch by type ──────────────────────────────────────

def test_apply_field_map_fills_text_via_fill_text():
    page = _StubPage({'input[name="email"]': {"visible": True}})
    specs = [{"key": "Email", "name": "email", "type": "text", "required": True}]
    result = apply_field_map(page, specs, {"Email": "a@b.invalid"})
    assert page.fills == [('input[name="email"]', "a@b.invalid")]
    assert result["filled"] == ["Email"]
    assert result["required_empty"] == []


def test_apply_field_map_uploads_file_via_upload_file():
    page = _StubPage({'input[type="file"]': {"count": 1}})
    specs = [{
        "key": "__resume__", "label": "Resume", "type": "file",
        "required": True, "selectors": ['input[type="file"]'],
    }]
    result = apply_field_map(page, specs, {"__resume__": "/tmp/resume.pdf"})
    assert page.uploads == [('input[type="file"]', "/tmp/resume.pdf")]
    assert result["filled"] == ["Resume"]


def test_apply_field_map_pastes_textarea_via_paste_textarea():
    page = _StubPage({"textarea": {"visible": True}})
    specs = [{
        "key": "__cover_letter__", "label": "Cover Letter",
        "type": "textarea", "selectors": ["textarea"],
    }]
    result = apply_field_map(
        page, specs, {"__cover_letter__": "Dear team, ..."}
    )
    assert page.fills == [("textarea", "Dear team, ...")]
    assert result["filled"] == ["Cover Letter"]


def test_apply_field_map_selects_option_via_select_handler():
    page = _StubPage({'select[name="src"]': {"visible": True}})
    specs = [{
        "key": "Source", "label": "How did you hear",
        "type": "select", "selectors": ['select[name="src"]'],
    }]
    result = apply_field_map(page, specs, {"Source": "LinkedIn"})
    assert page.selects == [('select[name="src"]', "LinkedIn")]
    assert result["filled"] == ["How did you hear"]


# ── apply_field_map: required-empty tracking ───────────────────────────────

def test_apply_field_map_reports_required_field_with_no_value_as_empty():
    # Email's field is present (visible) so it fills cleanly; Phone has no
    # value at all -> only Phone should be reported empty.
    page = _StubPage({'input[name="email"]': {"visible": True}})
    specs = [
        {"key": "Email", "name": "email", "type": "text", "required": True},
        {"key": "Phone", "name": "phone", "type": "text", "required": True},
    ]
    result = apply_field_map(page, specs, {"Email": "a@b.invalid"})
    assert "Phone" in result["required_empty"]
    assert "Email" not in result["required_empty"]
    assert result["filled"] == ["Email"]


def test_apply_field_map_reports_required_field_that_could_not_be_filled():
    """A required field WITH a value but NO matching selector on the page
    is still 'empty' on the form — it must show up in required_empty."""
    page = _StubPage()  # every selector invisible / count 0 → fill misses
    specs = [{"key": "Email", "name": "email", "type": "text", "required": True}]
    result = apply_field_map(page, specs, {"Email": "a@b.invalid"})
    assert result["filled"] == []
    assert result["required_empty"] == ["Email"]


def test_apply_field_map_optional_unfilled_field_not_reported_as_required_empty():
    page = _StubPage()
    specs = [{"key": "GitHub", "name": "gh", "type": "text", "required": False}]
    result = apply_field_map(page, specs, {"GitHub": "github.example/x"})
    assert result["required_empty"] == []


# ── apply_field_map: graceful degradation ──────────────────────────────────

def test_apply_field_map_skips_specs_with_no_value_for_that_key():
    page = _StubPage({'input[name="email"]': {"visible": True}})
    specs = [
        {"key": "Email", "name": "email", "type": "text"},
        {"key": "Missing", "name": "missing", "type": "text"},
    ]
    # No value for "Missing" — must not crash, must not fill it.
    result = apply_field_map(page, specs, {"Email": "a@b.invalid"})
    assert result["filled"] == ["Email"]
    assert 'input[name="missing"]' not in page.locator_calls


def test_apply_field_map_label_defaults_to_key_when_label_absent():
    page = _StubPage({'input[name="email"]': {"visible": True}})
    specs = [{"key": "Email", "name": "email", "type": "text"}]
    result = apply_field_map(page, specs, {"Email": "a@b.invalid"})
    # filled label is the key when no explicit label given
    assert result["filled"] == ["Email"]


# ── apply_field_map: selector-chain construction (parity guarantees) ────────

def test_text_field_chain_is_name_attr_then_label_selectors():
    """A text spec with a ``name`` builds ``input[name=]`` + ``textarea[name=]``
    first, then the four label/aria/placeholder fallbacks — the exact order
    the per-ATS adapters used before the data-driven rewrite."""
    page = _StubPage()  # all invisible → walk the whole chain
    specs = [{"key": "First Name", "name": "job_application[first_name]"}]
    apply_field_map(page, specs, {"First Name": "Test"})
    assert page.locator_calls == [
        'input[name="job_application[first_name]"]',
        'textarea[name="job_application[first_name]"]',
        'label:has-text("First Name") input',
        'label:has-text("First Name") >> input',
        'input[aria-label="First Name"]',
        'input[placeholder*="First Name" i]',
    ]


def test_text_field_chain_appends_fuzzy_name_fallback_when_flagged():
    """Ashby text fields carry ``fuzzy_name_fallback`` — the chain gains the
    two ``input[name*=...]`` fuzzy selectors after the label fallbacks,
    matching the old ``_ashby_field_selectors`` helper exactly."""
    page = _StubPage()
    specs = [{"key": "First Name", "fuzzy_name_fallback": True}]
    apply_field_map(page, specs, {"First Name": "Test"})
    assert page.locator_calls == [
        'label:has-text("First Name") input',
        'label:has-text("First Name") >> input',
        'input[aria-label="First Name"]',
        'input[placeholder*="First Name" i]',
        'input[name*="first_name"]',
        'input[name*="firstname"]',
    ]


def test_explicit_selectors_lead_then_label_fallback_for_text():
    """Phone-style specs put an explicit ``selectors`` lead first (the
    ``input[type=tel]:visible`` intl-tel-input anchor) then the label
    fallbacks — file/textarea specs use the explicit list ONLY."""
    page = _StubPage()
    specs = [{
        "key": "Phone", "type": "text",
        "selectors": ['input[type="tel"]:visible', 'input[id="phone"]'],
    }]
    apply_field_map(page, specs, {"Phone": "+1-555-0100"})
    assert page.locator_calls == [
        'input[type="tel"]:visible',
        'input[id="phone"]',
        'label:has-text("Phone") input',
        'label:has-text("Phone") >> input',
        'input[aria-label="Phone"]',
        'input[placeholder*="Phone" i]',
    ]


def test_file_field_chain_is_explicit_selectors_only_no_label_fallback():
    page = _StubPage()
    specs = [{
        "key": "__resume__", "label": "Resume", "type": "file",
        "selectors": ['input[type="file"][name="resume"]', 'input[type="file"]'],
    }]
    apply_field_map(page, specs, {"__resume__": "/tmp/r.pdf"})
    assert page.locator_calls == [
        'input[type="file"][name="resume"]',
        'input[type="file"]',
    ]
