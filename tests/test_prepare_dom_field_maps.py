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
    run_field_map_fill,
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
        discard_fill: bool = False,
        attrs: Optional[dict] = None,
    ):
        self.selector = selector
        self.page = page
        self.visible = visible
        self._count = count
        # honest-fill-verification hook (P0 #2): when True, ``fill`` /
        # ``select_option`` record the attempt but do NOT persist the value
        # on the page — simulating a React form that silently discards a
        # ``.fill()`` call. ``input_value()`` then reads back empty, which
        # is exactly the "didn't stick" case the DOM re-read must catch.
        self.discard_fill = discard_fill
        self._attrs = attrs or {}

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
        if not self.discard_fill:
            self.page.values[self.selector] = value

    def set_input_files(self, file_path: str) -> None:
        self.page.uploads.append((self.selector, file_path))

    def select_option(self, value: str) -> None:
        self.page.selects.append((self.selector, value))
        if not self.discard_fill:
            self.page.values[self.selector] = value

    def input_value(self) -> str:
        return self.page.values.get(self.selector, "")

    def get_attribute(self, name: str) -> Optional[str]:
        return self._attrs.get(name)

    def text_content(self) -> str:
        return self._attrs.get("_text", "")


class _StubPage:
    """Each selector resolves to a pre-configured locator. Unknown selectors
    resolve to an invisible / count==0 locator so the helper falls through."""

    def __init__(self, behaviors: Optional[dict] = None):
        self.behaviors = behaviors or {}
        self.fills: list[tuple[str, str]] = []
        self.uploads: list[tuple[str, str]] = []
        self.selects: list[tuple[str, str]] = []
        self.locator_calls: list[str] = []
        # Persists across separately-constructed _StubLocator instances for
        # the same selector, so a later ``read_value`` call (a fresh
        # ``page.locator(...)``) sees what an earlier ``fill()`` set.
        self.values: dict[str, str] = {}

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
    the per-ATS adapters used before the data-driven rewrite.

    ``"iframe"`` calls are filtered out of the recorded locator_calls before
    the order assertion: the frame-aware resolver (Task 1) probes for
    iframes after every top-document miss, which is a separate concern from
    the chain-CONSTRUCTION order this test pins — the stub Page has no
    iframes configured, so every probe finds none and contributes nothing.
    """
    page = _StubPage()  # all invisible → walk the whole chain
    specs = [{"key": "First Name", "name": "job_application[first_name]"}]
    apply_field_map(page, specs, {"First Name": "Test"})
    calls = [c for c in page.locator_calls if c != "iframe"]
    assert calls == [
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
    matching the old ``_ashby_field_selectors`` helper exactly. (``"iframe"``
    probe calls filtered out — see the previous test's docstring.)"""
    page = _StubPage()
    specs = [{"key": "First Name", "fuzzy_name_fallback": True}]
    apply_field_map(page, specs, {"First Name": "Test"})
    calls = [c for c in page.locator_calls if c != "iframe"]
    assert calls == [
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
    fallbacks — file/textarea specs use the explicit list ONLY. (``"iframe"``
    probe calls filtered out — see the first chain-order test's docstring.)
    """
    page = _StubPage()
    specs = [{
        "key": "Phone", "type": "text",
        "selectors": ['input[type="tel"]:visible', 'input[id="phone"]'],
    }]
    apply_field_map(page, specs, {"Phone": "+1-555-0100"})
    calls = [c for c in page.locator_calls if c != "iframe"]
    assert calls == [
        'input[type="tel"]:visible',
        'input[id="phone"]',
        'label:has-text("Phone") input',
        'label:has-text("Phone") >> input',
        'input[aria-label="Phone"]',
        'input[placeholder*="Phone" i]',
    ]


def test_file_field_chain_is_explicit_selectors_only_no_label_fallback():
    """(``"iframe"`` probe calls filtered out — see the first chain-order
    test's docstring.)"""
    page = _StubPage()
    specs = [{
        "key": "__resume__", "label": "Resume", "type": "file",
        "selectors": ['input[type="file"][name="resume"]', 'input[type="file"]'],
    }]
    apply_field_map(page, specs, {"__resume__": "/tmp/r.pdf"})
    calls = [c for c in page.locator_calls if c != "iframe"]
    assert calls == [
        'input[type="file"][name="resume"]',
        'input[type="file"]',
    ]


# ── apply_field_map: honest fill verification (DOM re-read, P0 #2) ─────────

def test_apply_field_map_counts_a_stuck_fill_as_filled():
    """A fill that matched a selector AND persisted on the DOM re-read
    counts as filled — the common (non-broken) case."""
    page = _StubPage({'input[name="email"]': {"visible": True}})
    specs = [{"key": "Email", "name": "email", "type": "text", "required": True}]
    result = apply_field_map(page, specs, {"Email": "a@b.invalid"})
    assert result["filled"] == ["Email"]
    assert result["required_empty"] == []


def test_apply_field_map_demotes_a_discarded_text_fill_to_required_empty():
    """A React-style form that silently discards ``.fill()`` — the selector
    matched and ``fill()`` didn't raise, but the DOM value never actually
    changed. The old contract (selector matched => filled) would report
    this as filled; the DOM re-read must catch it and report the required
    field as still empty."""
    page = _StubPage({
        'input[name="email"]': {"visible": True, "discard_fill": True},
    })
    specs = [{"key": "Email", "name": "email", "type": "text", "required": True}]
    result = apply_field_map(page, specs, {"Email": "a@b.invalid"})
    assert result["filled"] == []
    assert result["required_empty"] == ["Email"]


def test_apply_field_map_demotes_a_discarded_optional_fill_silently():
    """A discarded fill on an OPTIONAL field just drops out of ``filled`` —
    it must not show up in ``required_empty`` (only required gaps do)."""
    page = _StubPage({
        'input[name="gh"]': {"visible": True, "discard_fill": True},
    })
    specs = [{"key": "GitHub", "name": "gh", "type": "text", "required": False}]
    result = apply_field_map(page, specs, {"GitHub": "github.example/x"})
    assert result["filled"] == []
    assert result["required_empty"] == []


def test_apply_field_map_demotes_a_discarded_textarea_and_select_fill():
    page = _StubPage({
        "textarea": {"visible": True, "discard_fill": True},
        "select-a": {"visible": True, "discard_fill": True},
    })
    specs = [
        {"key": "__cover_letter__", "label": "Cover Letter", "type": "textarea",
         "selectors": ["textarea"]},
        {"key": "Source", "label": "How did you hear", "type": "select",
         "required": True, "selectors": ["select-a"]},
    ]
    result = apply_field_map(
        page, specs, {"__cover_letter__": "Dear team, ...", "Source": "LinkedIn"},
    )
    assert result["filled"] == []
    assert result["required_empty"] == ["How did you hear"]


def test_run_field_map_fill_reports_required_total_from_yaml_when_dom_scan_empty(tmp_path):
    """When the DOM scan finds nothing (the stub page doesn't recognize the
    generic ``[required], [aria-required=...]`` selector), the denominator
    falls back to exactly the YAML-declared required count — no behaviour
    change for ATSes/pages the scan can't see into."""
    class _FakeApplicant:
        def take_screenshot(self, page, label="form"):
            return f"/tmp/{label}.png"

    resume = tmp_path / "resume.pdf"
    resume.write_bytes(b"%PDF-1.4 fake")

    behaviors = {
        'input[name="job_application[first_name]"]': {"visible": True},
        'input[name="job_application[last_name]"]': {"visible": True},
        'input[name="job_application[email]"]': {"visible": True},
        'input[type="tel"]:visible': {"visible": True},
        'input[type="file"][name="job_application[resume]"]': {"count": 1},
    }
    page = _StubPage(behaviors)
    job = {"id": "gh-clean", "form_answers": {
        "first_name": "Test", "last_name": "Applicant",
        "email": "t@e.invalid", "phone": "+1-555-0100",
    }}

    result = run_field_map_fill(
        _FakeApplicant(), page, job, "greenhouse",
        screenshot_label="gh_clean", resume_path=str(resume),
    )

    assert result["required_total"] == 5  # First/Last/Email/Phone/Resume
    assert result["required_empty"] == []
    assert result["success"] is True


def test_run_field_map_fill_widens_required_set_with_dom_scanned_custom_question(tmp_path):
    """A role-specific question the ATS marks required in the live DOM (but
    field_maps.yml has never heard of — custom questions are policy-never-
    auto-filled) must widen the required-set denominator AND fail the
    cleanliness gate when left empty, even though every YAML-declared field
    filled cleanly."""
    class _FakeApplicant:
        def take_screenshot(self, page, label="form"):
            return f"/tmp/{label}.png"

    resume = tmp_path / "resume.pdf"
    resume.write_bytes(b"%PDF-1.4 fake")

    behaviors = {
        'input[name="job_application[first_name]"]': {"visible": True},
        'input[name="job_application[last_name]"]': {"visible": True},
        'input[name="job_application[email]"]': {"visible": True},
        'input[type="tel"]:visible': {"visible": True},
        'input[type="file"][name="job_application[resume]"]': {"count": 1},
        '[required], [aria-required="true"]': {
            "count": 1,
            "attrs": {"aria-label": "Are you authorized to work in the US?"},
        },
    }
    page = _StubPage(behaviors)
    job = {"id": "gh-custom", "form_answers": {
        "first_name": "Test", "last_name": "Applicant",
        "email": "t@e.invalid", "phone": "+1-555-0100",
    }}

    result = run_field_map_fill(
        _FakeApplicant(), page, job, "greenhouse",
        screenshot_label="gh_custom", resume_path=str(resume),
    )

    assert result["required_total"] == 6  # 5 YAML + 1 DOM-only custom question
    assert result["required_empty"] == ["Are you authorized to work in the US?"]
    assert result["success"] is False


def test_apply_field_map_file_upload_is_exempt_from_dom_reread():
    """File inputs never get a DOM value re-read (Playwright can't read one
    back) — ``upload_file``'s own count()-based success stands alone."""
    page = _StubPage({'input[type="file"]': {"count": 1}})
    specs = [{
        "key": "__resume__", "label": "Resume", "type": "file", "required": True,
        "selectors": ['input[type="file"]'],
    }]
    result = apply_field_map(page, specs, {"__resume__": "/tmp/r.pdf"})
    assert result["filled"] == ["Resume"]
    assert result["required_empty"] == []
