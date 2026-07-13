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
        raise_on_visible: bool = False,
        nodes: Optional[list] = None,
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
        # fill_report / misses coverage (Task 3 / #1): simulates a selector
        # that resolves but blows up on the visibility check (a detached
        # node, an inaccessible frame) — the primitive must swallow it AND
        # (when a caller passes ``misses``) record ``{"selector", "error"}``.
        self.raise_on_visible = raise_on_visible
        # Multi-element behind one selector (scoped-textarea enumeration
        # fix): each dict in ``nodes`` is a set of kwargs for one DISTINCT
        # candidate. When given, ``count()`` reports ``len(nodes)`` and
        # ``nth(i)`` resolves a fresh ``_StubLocator`` for that node
        # (tagged ``f"{selector}#{i}"`` in ``page.fills``) instead of the
        # single shared-instance behavior every other stub locator here
        # relies on. Mirrors ``test_prepare_dom_common.py``'s identical
        # extension.
        self._nodes = nodes

    @property
    def first(self):
        if self._nodes:
            return self.nth(0)
        return self

    def is_visible(self, timeout: int = 1000) -> bool:
        if self.raise_on_visible:
            raise RuntimeError(f"stub: is_visible blew up for {self.selector}")
        return self.visible

    def count(self) -> int:
        if self._nodes is not None:
            return len(self._nodes)
        return self._count

    def nth(self, i: int) -> "_StubLocator":
        if self._nodes is not None:
            return _StubLocator(f"{self.selector}#{i}", self.page, **self._nodes[i])
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

    def input_value(self, timeout: int = 1000) -> str:
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
    # "text=resume.pdf" is the upload-completion evidence upload_file polls
    # for (Task 2) — registering it with count:1 means the very first check
    # confirms immediately, so this test stays instant.
    page = _StubPage({
        'input[type="file"]': {"count": 1},
        "text=resume.pdf": {"count": 1},
    })
    specs = [{
        "key": "__resume__", "label": "Resume", "type": "file",
        "required": True, "selectors": ['input[type="file"]'],
    }]
    result = apply_field_map(page, specs, {"__resume__": "/tmp/resume.pdf"})
    assert page.uploads == [('input[type="file"]', "/tmp/resume.pdf")]
    assert result["filled"] == ["Resume"]


def test_apply_field_map_uploads_file_matched_but_not_confirmed_is_not_filled():
    """A selector matched and ``set_input_files`` didn't raise, but the ATS's
    async accept never showed up in the DOM within the (tiny, test-scale)
    confirm window — Task 2's honesty fix. Uses ``run_field_map_fill``'s
    plain ``apply_field_map`` path directly with the default confirm timeout
    would be correct in production, but a 3s real sleep is too slow for this
    suite — the field spec still exercises the exact same code path
    (``upload_file`` -> ``confirmed=False`` -> not filled); the timeout
    itself is already covered directly against ``upload_file`` in
    ``test_prepare_dom_common.py``, so this test only needs to confirm
    ``apply_field_map`` treats an unconfirmed upload as NOT filled and
    reports the required field as still empty."""
    import jobpipe.submit.adapters.prepare_dom.field_maps as field_maps_module

    original = field_maps_module.upload_file
    try:
        field_maps_module.upload_file = lambda *a, **k: (True, False)  # matched, unconfirmed
        page = _StubPage({'input[type="file"]': {"count": 1}})
        specs = [{
            "key": "__resume__", "label": "Resume", "type": "file",
            "required": True, "selectors": ['input[type="file"]'],
        }]
        result = apply_field_map(page, specs, {"__resume__": "/tmp/resume.pdf"})
        assert result["filled"] == []
        assert result["required_empty"] == ["Resume"]
    finally:
        field_maps_module.upload_file = original


def test_apply_field_map_pastes_textarea_via_paste_textarea():
    # Task 3 / #3: the bare catch-all "textarea" selector is now scoped for
    # the __cover_letter__ key — it only accepts a textarea whose resolved
    # label contains a cover-letter needle, so this stub textarea carries
    # one (a realistic ATS would label its lone textarea somehow).
    # ``count: 1`` models "one element exists here" — the scoped path now
    # enumerates via count()+nth(i) rather than checking only ``.first``.
    page = _StubPage({
        "textarea": {
            "visible": True, "count": 1,
            "attrs": {"aria-label": "Cover Letter"},
        },
    })
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


def test_apply_field_map_dispatches_combobox_type_to_select_combobox():
    """The ``combobox`` type spec dispatches to ``select_combobox`` —
    verified by monkeypatching it, since the full click-open-type-pick
    interaction already has dedicated stub-Page coverage in
    ``test_prepare_dom_common.py``. This test's only job is the wiring:
    right primitive, right selector chain, right value, and a True return
    counts as filled."""
    import jobpipe.submit.adapters.prepare_dom.field_maps as field_maps_module

    calls = []
    original = field_maps_module.select_combobox

    def fake_select_combobox(page, selectors, value, *, log=None, misses=None):
        calls.append((selectors, value))
        return True

    try:
        field_maps_module.select_combobox = fake_select_combobox
        page = _StubPage()
        specs = [{
            "key": "Work Authorization", "label": "Work Authorization",
            "type": "combobox", "required": True,
            "selectors": ['[role="combobox"][name="work_auth"]'],
        }]
        result = apply_field_map(page, specs, {"Work Authorization": "U.S. Citizen"})
        assert result["filled"] == ["Work Authorization"]
        assert result["required_empty"] == []
        assert calls == [(['[role="combobox"][name="work_auth"]'], "U.S. Citizen")]
    finally:
        field_maps_module.select_combobox = original


def test_apply_field_map_combobox_required_and_unfilled_reports_empty():
    """No stubbing of ``select_combobox`` here — the plain ``_StubPage``'s
    locators all default to invisible, so the real primitive correctly finds
    no visible container and returns False; the required field must show up
    in ``required_empty``."""
    page = _StubPage()
    specs = [{
        "key": "Work Authorization", "label": "Work Authorization",
        "type": "combobox", "required": True,
        "selectors": ['[role="combobox"][name="work_auth"]'],
    }]
    result = apply_field_map(page, specs, {"Work Authorization": "U.S. Citizen"})
    assert result["filled"] == []
    assert result["required_empty"] == ["Work Authorization"]


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
    # Task 3 / #3: give the textarea a cover-letter-matching label so the
    # NEW scoping check accepts it — this test's actual target is the P0
    # discard_fill demotion (paste "succeeds" but the DOM re-read shows it
    # never stuck), not the scoping check, so the label must resolve
    # cleanly to isolate that.
    page = _StubPage({
        "textarea": {
            "visible": True, "count": 1, "discard_fill": True,
            "attrs": {"aria-label": "Cover Letter"},
        },
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
        # upload-completion evidence (Task 2) — present immediately so the
        # upload confirms on the first poll instead of running out the
        # (production-scale) default confirm timeout.
        "text=resume.pdf": {"count": 1},
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
        "text=resume.pdf": {"count": 1},  # upload-completion evidence (Task 2)
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
    """File inputs never get a DOM value re-read via ``read_value``
    (Playwright can't read an ``input[type=file]``'s value back) — instead
    ``upload_file``'s own upload-completion wait (Task 2) stands in as the
    honesty check. Registering the completion evidence up front means it
    confirms on the very first poll."""
    page = _StubPage({
        'input[type="file"]': {"count": 1},
        "text=r.pdf": {"count": 1},
    })
    specs = [{
        "key": "__resume__", "label": "Resume", "type": "file", "required": True,
        "selectors": ['input[type="file"]'],
    }]
    result = apply_field_map(page, specs, {"__resume__": "/tmp/r.pdf"})
    assert result["filled"] == ["Resume"]
    assert result["required_empty"] == []


# ── fill_report (Task 3 / #1) ───────────────────────────────────────────────
#
# Before Task 3, ``apply_field_map`` only ever recorded ``required_empty`` —
# an OPTIONAL field that silently failed left no trace anywhere, and no per-
# selector detail (which candidate matched, which raised what) existed at
# all. ``fill_report`` is a new, separate record: one entry per spec,
# whether or not it had a value, carrying ``matched_selector`` /
# ``value_verified`` / a per-selector ``misses`` log.

def test_fill_report_has_one_entry_per_spec_including_unattempted():
    page = _StubPage({'input[name="email"]': {"visible": True}})
    specs = [
        {"key": "Email", "name": "email", "type": "text", "required": True},
        {"key": "Phone", "name": "phone", "type": "text", "required": False},
    ]
    # No value supplied for Phone at all.
    result = apply_field_map(page, specs, {"Email": "a@b.invalid"})
    report = result["fill_report"]
    assert [e["key"] for e in report] == ["Email", "Phone"]

    email_entry = report[0]
    assert email_entry["label"] == "Email"
    assert email_entry["type"] == "text"
    assert email_entry["required"] is True
    assert email_entry["attempted"] is True
    assert email_entry["matched_selector"] == 'input[name="email"]'
    assert email_entry["value_verified"] is True
    assert email_entry["misses"] == []

    phone_entry = report[1]
    assert phone_entry["attempted"] is False
    assert phone_entry["matched_selector"] is None
    assert phone_entry["value_verified"] is False
    assert phone_entry["misses"] == []


def test_fill_report_records_per_selector_misses_for_optional_field():
    """An OPTIONAL field that never matches anything used to vanish
    completely (only required fields showed up in ``required_empty``).
    ``fill_report`` must still carry it, including the exception raised by
    the selector that blew up along the way."""
    page = _StubPage({
        "sel-a": {"raise_on_visible": True},
        "sel-b": {"visible": False},
    })
    specs = [{
        "key": "GitHub", "label": "GitHub", "type": "text", "required": False,
        "selectors": ["sel-a", "sel-b"],
    }]
    result = apply_field_map(page, specs, {"GitHub": "github.example/x"})
    assert result["required_empty"] == []  # optional — old contract unchanged

    entry = result["fill_report"][0]
    assert entry["attempted"] is True
    assert entry["matched_selector"] is None
    assert entry["value_verified"] is False
    assert entry["misses"] == [{"selector": "sel-a", "error": "RuntimeError"}]


def test_fill_report_records_matched_selector_for_file_upload():
    page = _StubPage({
        'input[type="file"]': {"count": 1},
        "text=r.pdf": {"count": 1},
    })
    specs = [{
        "key": "__resume__", "label": "Resume", "type": "file", "required": True,
        "selectors": ['input[type="file"]'],
    }]
    result = apply_field_map(page, specs, {"__resume__": "/tmp/r.pdf"})
    entry = result["fill_report"][0]
    assert entry["matched_selector"] == 'input[type="file"]'
    assert entry["value_verified"] is True
    assert entry["misses"] == []


def test_fill_report_demoted_fill_shows_matched_selector_but_not_verified():
    """A fill that matched a selector but didn't stick on DOM re-read (P0
    honesty fix) still records WHICH selector matched — ``value_verified``
    is what turns False, not ``matched_selector``."""
    page = _StubPage({
        'input[name="email"]': {"visible": True, "discard_fill": True},
    })
    specs = [{"key": "Email", "name": "email", "type": "text", "required": True}]
    result = apply_field_map(page, specs, {"Email": "a@b.invalid"})
    entry = result["fill_report"][0]
    assert entry["matched_selector"] == 'input[name="email"]'
    assert entry["value_verified"] is False
    assert result["required_empty"] == ["Email"]


# ── Scoped cover-letter textarea fallback (Task 3 / #3) ─────────────────────
#
# The bare catch-all ``'textarea'`` at the end of every ATS's cover-letter
# selector chain used to grab whichever textarea rendered first — often a
# custom question, not the cover letter, on a form with no properly labeled
# cover-letter box. ``apply_field_map`` now scopes that catch-all (for the
# ``__cover_letter__`` spec only) to a textarea whose resolved label
# contains "cover" or "letter".

def test_cover_letter_bare_textarea_skips_mislabeled_custom_question():
    """Only one textarea on the page, and it's an unrelated custom
    question (aria-label doesn't mention cover/letter) — the bare catch-all
    must NOT paste the cover letter into it."""
    page = _StubPage({
        "textarea": {
            "visible": True, "count": 1,
            "attrs": {"aria-label": "Why do you want this role?"},
        },
    })
    specs = [{
        "key": "__cover_letter__", "label": "Cover Letter",
        "type": "textarea", "selectors": ["textarea"],
    }]
    result = apply_field_map(
        page, specs, {"__cover_letter__": "Dear team, ..."}
    )
    assert page.fills == []
    assert result["filled"] == []
    entry = result["fill_report"][0]
    assert entry["matched_selector"] is None
    assert entry["value_verified"] is False


def test_cover_letter_bare_textarea_accepts_properly_labeled_textarea():
    """The bare catch-all textarea IS labeled "Cover Letter" — the scoped
    check must still accept it (scoping isn't a blanket ban, just a label
    requirement)."""
    page = _StubPage({
        "textarea": {
            "visible": True, "count": 1,
            "attrs": {"aria-label": "Cover Letter"},
        },
    })
    specs = [{
        "key": "__cover_letter__", "label": "Cover Letter",
        "type": "textarea", "selectors": ["textarea"],
    }]
    result = apply_field_map(
        page, specs, {"__cover_letter__": "Dear team, ..."}
    )
    assert page.fills == [("textarea", "Dear team, ...")]
    assert result["filled"] == ["Cover Letter"]
    assert result["fill_report"][0]["matched_selector"] == "textarea"


def test_cover_letter_bare_textarea_fills_the_correctly_labeled_one_when_a_mislabeled_textarea_renders_first():
    """The review finding this test covers: the OLD scoped check only ever
    inspected ``page.locator("textarea").first`` — on a form whose FIRST
    textarea is an unrelated custom question and a correctly labeled
    "Cover Letter" textarea renders SECOND, the old code stopped the wrong
    behavior (no more mis-filling the custom question) but never delivered
    the right one, silently filling nothing. This is the brief's own
    worked example, exercised through the full ``apply_field_map`` path
    (not just the ``paste_textarea`` primitive) to prove the acceptance
    bar is actually met: the cover letter lands in the correctly labeled
    textarea, not nowhere."""
    page = _StubPage({
        "textarea": {"nodes": [
            {"visible": True, "attrs": {"aria-label": "Why do you want this role?"}},
            {"visible": True, "attrs": {"aria-label": "Cover Letter"}},
        ]},
    })
    specs = [{
        "key": "__cover_letter__", "label": "Cover Letter",
        "type": "textarea", "selectors": ["textarea"],
    }]
    result = apply_field_map(
        page, specs, {"__cover_letter__": "Dear team, ..."}
    )
    assert page.fills == [("textarea#1", "Dear team, ...")]
    assert result["filled"] == ["Cover Letter"]
    assert result["fill_report"][0]["matched_selector"] == "textarea"


def test_cover_letter_more_specific_selector_ahead_of_catch_all_is_unscoped():
    """A qualified selector (``textarea[name*="cover" i]``) ahead of the
    bare catch-all is tried unconditionally — scoping only applies to the
    literal bare ``"textarea"`` candidate."""
    page = _StubPage({
        'textarea[name*="cover" i]': {"visible": True},
        # No attrs at all — if this were scoped it would be rejected, but
        # it must never even reach the scoping check.
    })
    specs = [{
        "key": "__cover_letter__", "label": "Cover Letter",
        "type": "textarea",
        "selectors": ['textarea[name*="cover" i]', "textarea"],
    }]
    result = apply_field_map(
        page, specs, {"__cover_letter__": "Dear team, ..."}
    )
    assert result["filled"] == ["Cover Letter"]
    assert page.fills == [('textarea[name*="cover" i]', "Dear team, ...")]


def test_non_cover_letter_textarea_spec_is_not_scoped():
    """Scoping is tied to the ``__cover_letter__`` key specifically — a
    hypothetical other textarea-type spec must not be scoped (no needle
    check applied) since ``scoped_needles`` is only ever passed for the
    cover-letter key."""
    page = _StubPage({
        "textarea": {"visible": True, "attrs": {"aria-label": "Random question"}},
    })
    specs = [{
        "key": "OtherTextarea", "label": "Other Textarea",
        "type": "textarea", "selectors": ["textarea"],
    }]
    result = apply_field_map(
        page, specs, {"OtherTextarea": "some answer"}
    )
    assert result["filled"] == ["Other Textarea"]
