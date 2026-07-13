"""Direct unit tests for ``jobpipe.submit.adapters.prepare_dom._common``.

PR-7 introduces a shared module of sync Playwright helpers for the
prepare-only adapters. These tests exercise every helper against a stub
``Page`` object so the suite can run with no Playwright install — the helpers
are deliberately duck-typed (only Page methods like ``locator``, ``first``,
``is_visible``, ``click``, ``fill``, ``count``, ``set_input_files`` are used).
"""

from __future__ import annotations

from typing import Optional

from jobpipe.submit.adapters.prepare_dom._common import (
    build_field_map,
    fill_text,
    label_selectors,
    load_cover_letter,
    name_attr_selectors,
    note_unfilled_custom_questions,
    paste_textarea,
    read_value,
    scan_required_fields,
    select_option,
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
        discard_fill: bool = False,
        attrs: Optional[dict] = None,
    ):
        self.selector = selector
        self.page = page
        self.visible = visible
        self._count = count
        self.raise_on_visible = raise_on_visible
        self.clicked = False
        self.filled_with: Optional[str] = None
        self.uploaded: Optional[str] = None
        # honest-fill-verification hook (P0 #2): a discarded fill is
        # recorded as attempted but never persists, so a later
        # ``input_value()`` read-back reports empty (fill didn't stick).
        self.discard_fill = discard_fill
        self._attrs = attrs or {}

    @property
    def first(self):
        return self

    def is_visible(self, timeout: int = 1000) -> bool:
        if self.raise_on_visible:
            raise RuntimeError(f"stub: is_visible blew up for {self.selector}")
        return self.visible

    def count(self) -> int:
        return self._count

    def nth(self, i: int) -> "_StubLocator":
        return self

    def click(self) -> None:
        self.clicked = True

    def fill(self, value: str) -> None:
        self.filled_with = value
        self.page.fills.append((self.selector, value))
        if not self.discard_fill:
            self.page.values[self.selector] = value

    def set_input_files(self, file_path: str) -> None:
        self.uploaded = file_path
        self.page.uploads.append((self.selector, file_path))

    def select_option(self, value: str) -> None:
        self.selected = value
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
    """A page where each selector resolves to a pre-configured locator.

    Tests construct one with a dict of ``{selector: locator_kwargs}`` and the
    page returns matching locators. Unknown selectors return a locator that
    is invisible / count==0 so the helper falls through to the next.

    ``iframe_behaviors`` (frame + shadow-DOM piercing, Task 1) is an optional
    ``{frame_index: {selector: locator_kwargs}}`` map. When set, ``locator
    ("iframe")`` reports ``count() == len(iframe_behaviors)`` so the
    frame-aware primitives know how many iframes to walk, and
    ``frame_locator("iframe").nth(i).locator(selector)`` resolves against
    that frame's own behavior dict. Tests that never pass
    ``iframe_behaviors`` get ``count() == 0`` — no frames tried, byte-
    identical to the pre-Task-1 behavior."""

    def __init__(self, behaviors: dict, iframe_behaviors: Optional[dict] = None):
        self.behaviors = behaviors
        self.iframe_behaviors = iframe_behaviors or {}
        self.fills: list[tuple[str, str]] = []
        self.uploads: list[tuple[str, str]] = []
        self.selects: list[tuple[str, str]] = []
        self.locator_calls: list[str] = []
        # Persists filled/selected values across separately-constructed
        # locator instances for the same selector (mirrors a real DOM).
        self.values: dict[str, str] = {}

    def locator(self, selector: str) -> _StubLocator:
        self.locator_calls.append(selector)
        if selector == "iframe":
            return _StubLocator(selector, self, count=len(self.iframe_behaviors))
        kwargs = self.behaviors.get(selector, {})
        return _StubLocator(selector, self, **kwargs)

    def frame_locator(self, selector: str) -> "_StubFrameLocator":
        return _StubFrameLocator(self)


class _StubFrameLocator:
    """Stand-in for Playwright's ``FrameLocator`` — scoped to one iframe once
    ``.nth(i)`` picks an index. ``.locator(selector)`` resolves against that
    frame's own behavior dict (``page.iframe_behaviors[index]``), keyed under
    ``"iframe[{index}] {selector}"`` so fills/values are trackable per-frame
    without colliding with the top document's own selector namespace."""

    def __init__(self, page: _StubPage, index: Optional[int] = None):
        self.page = page
        self.index = index

    def nth(self, i: int) -> "_StubFrameLocator":
        return _StubFrameLocator(self.page, i)

    def locator(self, selector: str) -> _StubLocator:
        kwargs = self.page.iframe_behaviors.get(self.index, {}).get(selector, {})
        return _StubLocator(f"iframe[{self.index}] {selector}", self.page, **kwargs)


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
    # sel-a's top-doc miss triggers a frame-aware fallback probe ("iframe")
    # before the loop moves on to sel-b (no iframes configured -> 0 found).
    assert page.locator_calls == ["sel-a", "iframe", "sel-b"]


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
    from jobpipe.submit.adapters.prepare_dom.field_maps import (
        _selectors_for,
        load_field_map,
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

    # The phone chain now comes from the greenhouse field map, not a module
    # constant — build it exactly as apply_field_map would.
    phone_spec = next(
        s for s in load_field_map("greenhouse") if s["key"] == "Phone"
    )
    full_chain = _selectors_for(phone_spec, "Phone", "text")
    ok = fill_text(page, full_chain, "+1-555-0100")

    assert ok is True
    assert page.fills == [('input[type="tel"]:visible', "+1-555-0100")]
    # Walk-through stops at the first visible match — label fallback
    # never gets queried.
    assert 'label:has-text("Phone") input' not in page.locator_calls


def test_phone_selectors_canonical_order_per_ats():
    """The three per-ATS phone specs all lead with
    ``input[type="tel"]:visible`` (the intl-tel-input anchor) so the
    fix has uniform shape across Greenhouse, Lever, and Ashby. Per-ATS
    fallbacks differ (Greenhouse pins ``name="job_application[phone]"``,
    Lever pins ``name="phone"``, Ashby has no canonical name and skips
    that step) — verify the leading selector is identical. The chains now
    live in ``field_maps.yml`` rather than per-module constants.
    """
    from jobpipe.submit.adapters.prepare_dom.field_maps import load_field_map

    for ats in ("greenhouse", "lever", "ashby"):
        phone_spec = next(
            s for s in load_field_map(ats) if s["key"] == "Phone"
        )
        selectors = phone_spec["selectors"]
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


# ── select_option ──────────────────────────────────────────────────────────

def test_select_option_selects_first_visible_match():
    page = _StubPage({
        "select-a": {"visible": False},
        "select-b": {"visible": True},
    })
    ok = select_option(page, ["select-a", "select-b"], "LinkedIn")
    assert ok is True
    assert page.selects == [("select-b", "LinkedIn")]
    # select-a's top-doc miss triggers a frame-aware fallback probe
    # ("iframe") before the loop moves on to select-b.
    assert page.locator_calls == ["select-a", "iframe", "select-b"]


def test_select_option_returns_false_when_no_visible_select():
    page = _StubPage({"select-a": {"visible": False}})
    ok = select_option(page, ["select-a"], "ignored")
    assert ok is False
    assert page.selects == []


def test_select_option_swallows_per_selector_exceptions():
    page = _StubPage({
        "select-a": {"raise_on_visible": True},
        "select-b": {"visible": True},
    })
    ok = select_option(page, ["select-a", "select-b"], "X")
    assert ok is True
    assert page.selects == [("select-b", "X")]


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


# ── read_value (honest fill verification, P0 #2) ───────────────────────────

def test_read_value_returns_value_from_matching_selector():
    page = _StubPage({})
    page.values["sel-a"] = "hello"
    assert read_value(page, ["sel-a", "sel-b"]) == "hello"


def test_read_value_falls_through_to_next_selector_when_first_is_empty():
    page = _StubPage({})
    page.values["sel-b"] = "world"
    assert read_value(page, ["sel-a", "sel-b"]) == "world"


def test_read_value_returns_empty_string_when_nothing_matches():
    page = _StubPage({})
    assert read_value(page, ["sel-a", "sel-b"]) == ""


def test_read_value_swallows_exceptions_per_selector():
    class _BoomLocator:
        def input_value(self):
            raise RuntimeError("not an input element")

        @property
        def first(self):
            return self

    class _BoomPage:
        def locator(self, selector):
            return _BoomLocator()

    assert read_value(_BoomPage(), ["sel-a"]) == ""


# ── scan_required_fields (form-derived required-set, P0 #2) ────────────────

class _ReqLocator:
    """Duck-typed Locator over a fixed node list — supports the subset
    ``scan_required_fields`` / ``_resolve_dom_label`` actually calls
    (``count``, ``nth``, ``first``, ``get_attribute``, ``text_content``)."""

    def __init__(self, nodes: list[dict]):
        self._nodes = nodes

    def count(self) -> int:
        return len(self._nodes)

    def nth(self, i: int) -> "_ReqLocator":
        return _ReqLocator([self._nodes[i]])

    @property
    def first(self) -> "_ReqLocator":
        return _ReqLocator(self._nodes[:1])

    def get_attribute(self, name: str):
        if not self._nodes:
            return None
        return self._nodes[0]["attrs"].get(name)

    def text_content(self) -> str:
        if not self._nodes:
            return ""
        return self._nodes[0].get("text", "")


class _ReqPage:
    """Page stand-in for the DOM-required scan: the combined
    ``[required], [aria-required="true"]`` query returns a fixed node list;
    ``label[for=id]`` queries resolve against a separate id->text map."""

    def __init__(self, required_nodes: list[dict], label_by_id: Optional[dict] = None):
        self._required_nodes = required_nodes
        self._label_by_id = label_by_id or {}

    def locator(self, selector: str) -> _ReqLocator:
        if selector == '[required], [aria-required="true"]':
            return _ReqLocator(self._required_nodes)
        if selector.startswith('label[for="'):
            el_id = selector.split('"')[1]
            text = self._label_by_id.get(el_id)
            if text is None:
                return _ReqLocator([])
            return _ReqLocator([{"attrs": {}, "text": text}])
        return _ReqLocator([])


def test_scan_required_fields_resolves_label_via_aria_label():
    page = _ReqPage([{"attrs": {"aria-label": "Willing to relocate?", "id": "q1"}}])
    result = scan_required_fields(page)
    assert result == [{"label": "Willing to relocate?", "selectors": ['[id="q1"]']}]


def test_scan_required_fields_falls_back_to_associated_label_element():
    page = _ReqPage(
        [{"attrs": {"id": "q2"}}],
        label_by_id={"q2": "How did you hear about us?"},
    )
    result = scan_required_fields(page)
    assert result == [
        {"label": "How did you hear about us?", "selectors": ['[id="q2"]']}
    ]


def test_scan_required_fields_falls_back_to_name_attribute():
    page = _ReqPage([{"attrs": {"name": "custom_question_1"}}])
    result = scan_required_fields(page)
    assert result == [
        {"label": "custom_question_1", "selectors": ['[name="custom_question_1"]']}
    ]


def test_scan_required_fields_dedupes_by_resolved_label():
    page = _ReqPage([
        {"attrs": {"aria-label": "Phone", "id": "a"}},
        {"attrs": {"aria-label": "Phone", "id": "b"}},
    ])
    assert len(scan_required_fields(page)) == 1


def test_scan_required_fields_skips_element_with_no_resolvable_label():
    page = _ReqPage([{"attrs": {}}])
    assert scan_required_fields(page) == []


def test_scan_required_fields_degrades_to_empty_on_scan_failure():
    class _BoomPage:
        def locator(self, selector):
            raise RuntimeError("selector engine blew up")

    assert scan_required_fields(_BoomPage()) == []


# ── Frame-aware resolution (frame + shadow-DOM piercing, Task 1) ───────────
#
# Every primitive is proven to fall through top-document -> iframe when the
# selector is absent from the top document but present inside an iframe, in
# each direction: found only in the top doc (no frame probe needed), found
# only in an iframe, found in neither, and a frame-access failure swallowed
# exactly like any other per-selector miss.

def test_fill_text_falls_through_from_top_document_into_iframe():
    page = _StubPage({}, iframe_behaviors={0: {"sel-a": {"visible": True}}})
    ok = fill_text(page, ["sel-a"], "value-iframe")
    assert ok is True
    assert page.fills == [("iframe[0] sel-a", "value-iframe")]


def test_upload_file_falls_through_from_top_document_into_iframe():
    page = _StubPage({}, iframe_behaviors={0: {"sel-a": {"count": 1}}})
    ok = upload_file(page, ["sel-a"], "/tmp/resume.pdf")
    assert ok is True
    assert page.uploads == [("iframe[0] sel-a", "/tmp/resume.pdf")]


def test_paste_textarea_falls_through_from_top_document_into_iframe():
    page = _StubPage({}, iframe_behaviors={0: {"textarea": {"visible": True}}})
    ok = paste_textarea(page, ["textarea"], "cover body")
    assert ok is True
    assert page.fills == [("iframe[0] textarea", "cover body")]


def test_select_option_falls_through_from_top_document_into_iframe():
    page = _StubPage({}, iframe_behaviors={0: {"select-a": {"visible": True}}})
    ok = select_option(page, ["select-a"], "LinkedIn")
    assert ok is True
    assert page.selects == [("iframe[0] select-a", "LinkedIn")]


def test_read_value_falls_through_from_top_document_into_iframe():
    """A value written into iframe index 0 must read back from that same
    frame — proves read_value uses the SAME frame-aware resolution as the
    fill primitives, so a successful frame-fill never gets misread as empty
    against the top document (which would wrongly demote it under P0's DOM
    re-read verification)."""
    page = _StubPage({}, iframe_behaviors={0: {"sel-a": {}}})
    page.values["iframe[0] sel-a"] = "hello-from-iframe"
    assert read_value(page, ["sel-a"]) == "hello-from-iframe"


def test_frame_iteration_visits_multiple_iframes_in_dom_order():
    """The selector matches only inside the SECOND iframe (index 1) — proves
    the walk doesn't stop after the first iframe and visits frames in DOM
    order rather than, say, only ever trying index 0."""
    page = _StubPage(
        {},
        iframe_behaviors={
            0: {},  # present but the selector isn't found in this frame
            1: {"sel-a": {"visible": True}},
        },
    )
    ok = fill_text(page, ["sel-a"], "value-2nd-frame")
    assert ok is True
    assert page.fills == [("iframe[1] sel-a", "value-2nd-frame")]


def test_fill_text_prefers_top_document_over_iframe():
    """When the same selector is visible in BOTH the top document and an
    iframe, the top document wins (it's searched first) — no ``"iframe"``
    probe is even needed."""
    page = _StubPage(
        {"sel-a": {"visible": True}},
        iframe_behaviors={0: {"sel-a": {"visible": True}}},
    )
    ok = fill_text(page, ["sel-a"], "value-top")
    assert ok is True
    assert page.fills == [("sel-a", "value-top")]
    assert "iframe" not in page.locator_calls


def test_fill_text_returns_false_when_selector_absent_from_frames_too():
    page = _StubPage({}, iframe_behaviors={0: {}, 1: {}})
    ok = fill_text(page, ["sel-a"], "value")
    assert ok is False
    assert page.fills == []


def test_frame_access_failure_is_swallowed_and_falls_through():
    """A Page whose ``frame_locator()`` raises (cross-origin restriction, a
    detached iframe) must not propagate — the primitive just reports no
    match, exactly like any other per-selector miss, never raises."""

    class _BrokenFramePage(_StubPage):
        def frame_locator(self, selector: str):
            raise RuntimeError("cross-origin frame — inaccessible")

    page = _BrokenFramePage(
        {}, iframe_behaviors={0: {"sel-a": {"visible": True}}}
    )
    ok = fill_text(page, ["sel-a"], "value")
    assert ok is False


def test_frame_locator_missing_entirely_is_swallowed():
    """An even more degenerate stub than a raising one: a Page whose
    ``"iframe"`` count is > 0 but that has no ``frame_locator()`` method at
    all. ``AttributeError`` must be swallowed the same as any other
    per-frame failure rather than propagating out of the primitive."""

    class _CountOnlyPage:
        def __init__(self):
            self.fills: list = []
            self.values: dict = {}

        def locator(self, selector: str):
            if selector == "iframe":
                return _StubLocator("iframe", self, count=2)
            return _StubLocator(selector, self, visible=False)

    page = _CountOnlyPage()
    assert fill_text(page, ["sel-a"], "value") is False
