"""tests/test_ats_html_fixtures.py — recorded-HTML regression fixtures (Task 6).

Every other prepare_dom test in this suite (``test_prepare_dom_common.py``,
``test_prepare_dom_field_maps.py``, ``test_prepare_dom_{greenhouse,lever,
ashby}.py``) exercises the fill primitives against a hand-written duck-typed
stub ``Page`` — fast, deterministic, no browser install required, but zero
proof any of it actually works against a real DOM. The source review that
produced the 6-task hardening plan called this out explicitly: "Zero tests
against real ATS HTML — 411 test functions but every prepare_dom test uses
fake Page objects; drift is only ever discovered in production."

This file is the deliberate exception: it drives real (headless) Chromium
via Playwright's ``sync_api`` against ``page.set_content()`` /
``page.goto("data:...")``-loaded, sanitized, hand-constructed HTML fixtures
under ``tests/fixtures/ats_html/{greenhouse,lever,ashby}/{standard,embed}.html``
— one direct-render case and one iframe-embed case per ATS (6 files total),
run through the ACTUAL ``run_field_map_fill`` / ``apply_field_map`` path
(the same call the three ``prepare_dom/{greenhouse,lever,ashby}.py``
adapters make in production), not a re-implementation of it.

Construction approach — why ``set_content`` + iframe ``srcdoc``, not a local
HTTP server: the fixtures are 100% static HTML with no dynamic behavior,
XHR, or bot-detection to defeat (real Greenhouse/Lever/Ashby pages ARE more
dynamic than this, but the thing under test is the SELECTOR CHAIN /
frame-piercing logic, which doesn't care how the DOM got there). A
``page.set_content()`` call for the "standard" fixtures and a top document
containing ``<iframe srcdoc="...">`` for the "embed" fixtures reproduces the
exact DOM shape the fill primitives see in production with far less
machinery than standing up and tearing down an HTTP server per test. The
embed fixtures' iframe is same-origin-equivalent (``about:srcdoc``), which
Playwright's ``frame_locator()`` reaches identically to a same-origin
cross-domain iframe would — the piercing logic under test
(``_iter_frame_locators``) doesn't distinguish the two.

Each fixture's HTML was hand-constructed (not scraped/recorded from a real
company) to stay sanitized — see each fixture file's own header comment for
exactly which selector-chain link it's designed to exercise (name-attr
match, phone's intl-tel-input anchor, label-wrap / aria-label / placeholder
fallbacks, the fuzzy ``input[name*=...]`` fallback, the scoped bare
"textarea" catch-all with a mislabeled decoy textarea ahead of it in DOM
order, and one required custom question ``field_maps.yml`` has never heard
of, to prove the DOM-required-set widening still catches it).

Coverage bar: these tests do NOT just assert "the fill ran without
exceptions" or "success is a bool" — they pin the exact ``fields_filled``
set, the exact ``required_empty`` set (proving both the happy-path fill AND
the honest-verification / DOM-widening gate are both live against a real
DOM), and spot-check ``fill_report[...]["matched_selector"]`` for the
fields whose fixture markup was deliberately built to only match ONE
specific link in the selector chain — a selector-chain regression (e.g. an
edit to ``field_maps.yml`` that drops the ``input[type="tel"]:visible``
phone anchor, or breaks the scoped-textarea label resolution) would flip
these assertions, not just a bare "did it crash" check.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from playwright.sync_api import sync_playwright

from jobpipe.submit.adapters.prepare_dom._common import wait_for_form_ready
from jobpipe.submit.adapters.prepare_dom.field_maps import run_field_map_fill

pytestmark = pytest.mark.ats_fixtures

FIXTURES_DIR = Path(__file__).parent / "fixtures" / "ats_html"

# load_cover_letter treats any string over 100 chars as inline cover-letter
# text (no on-disk file needed) — see _common.load_cover_letter.
_COVER_LETTER_TEXT = (
    "Dear Hiring Team, I'm excited about the opportunity to bring my "
    "robotics and applied-AI background to your team. I'd welcome the "
    "chance to discuss how my experience could contribute. Thank you for "
    "your time and consideration."
)


class _FakeApplicant:
    """Stands in for ``BaseApplicant`` — ``run_field_map_fill`` only ever
    calls ``take_screenshot(page, label=...)`` on it. Screenshotting isn't
    what this suite validates, so this returns a fixed path without
    touching the filesystem or calling ``page.screenshot()`` for real,
    keeping these tests fast and hermetic."""

    def take_screenshot(self, page, label: str = "form") -> str:
        return f"/tmp/{label}.png"


# ── Playwright browser/page fixtures ────────────────────────────────────────

@pytest.fixture(scope="module")
def browser():
    with sync_playwright() as p:
        b = p.chromium.launch()
        yield b
        b.close()


@pytest.fixture()
def page(browser):
    pg = browser.new_page()
    yield pg
    pg.close()


# ── Fixture-file loading ────────────────────────────────────────────────────

def _load_fixture(ats: str, variant: str) -> str:
    return (FIXTURES_DIR / ats / f"{variant}.html").read_text()


def _resume_path(tmp_path: Path) -> str:
    resume = tmp_path / "resume.pdf"
    resume.write_bytes(b"%PDF-1.4 fake resume content for fixture tests")
    return str(resume)


def _fill_report_by_label(result: dict) -> dict[str, dict]:
    return {entry["label"]: entry for entry in result["fill_report"]}


def _expected_required_empty(variant: str, standard_expected: set[str]) -> set[str]:
    """The DOM-required-set widening (``scan_required_fields`` /
    ``dom_field_has_value``) only ever inspects the TOP document — it is
    not frame-aware, a gap Task 1's review explicitly scoped out (same
    boundary as ``wait_for_form_ready``, see ``_run``'s embed branch
    above). On the ``embed`` variant, the custom required question lives
    entirely inside the iframe, so the widening pass never sees it at all
    — not "sees it and thinks it's answered," genuinely never discovers
    it — and ``required_empty`` is empty. On ``standard`` the question is
    on the top document and DOES get caught, per ``standard_expected``.
    """
    return standard_expected if variant == "standard" else set()


def _run(page, ats: str, variant: str, job: dict, resume_path: str, *,
          value_overrides: dict | None = None) -> dict:
    """Load the fixture, run the SAME readiness-wait + fill call sequence
    the real ``prepare_dom/{ats}.py`` adapters run, and return the fill
    result. No navigation/URL hop logic from the adapters is replicated
    here — that's per-ATS overview->form URL-rewriting, orthogonal to what
    this suite is validating (the fill primitives against real markup)."""
    page.set_content(_load_fixture(ats, variant))
    readiness = wait_for_form_ready(page, timeout_ms=2000, settle_s=0.1)
    if variant == "standard":
        assert readiness["ready"] is True, (
            f"{ats}/{variant}: wait_for_form_ready found no required-field/"
            f"form evidence — fixture markup regression, not a real-world "
            f"timeout"
        )
    else:
        # ``wait_for_form_ready`` (Task 3) only inspects the TOP document —
        # it is not frame-aware, unlike the fill primitives (Task 1) and
        # ``scan_required_fields`` (a gap Task 1's review explicitly scoped
        # out). A true embed's top document has no required-field/<form>
        # evidence of its own by construction (see the embed fixtures'
        # header comments) — readiness correctly (if bluntly) reports
        # ``ready: False`` here. That does NOT block the fill: production
        # code still calls ``run_field_map_fill`` regardless of readiness,
        # same as every adapter does today, and the assertions below prove
        # the frame-aware fill primitives succeed anyway.
        assert readiness["ready"] is False, (
            f"{ats}/{variant}: expected wait_for_form_ready to report "
            f"not-ready for a true embed (top document has no form "
            f"evidence by design) — got {readiness!r}. If this fixture's "
            f"top document gained a stray required field or <form> tag, "
            f"fix the fixture rather than this assertion."
        )
    return run_field_map_fill(
        _FakeApplicant(), page, job, ats,
        screenshot_label=f"{ats}_{variant}",
        resume_path=resume_path,
        cover_letter_path=_COVER_LETTER_TEXT,
        value_overrides=value_overrides,
        note_custom_questions=True,
        readiness_timeout=not readiness["ready"],
    )


# ── Greenhouse ───────────────────────────────────────────────────────────────

_GREENHOUSE_JOB = {
    "id": "gh-fixture",
    "form_answers": {
        "first_name": "Jordan",
        "last_name": "Rivera",
        "email": "jordan.rivera@example.invalid",
        "phone": "+1-555-0100",
        "linkedin_url": "https://linkedin.example/in/jordan-rivera",
        "current_location": "Atlanta, GA",
        "current_company": "Prior Robotics Co",
        "current_title": "Software Engineer",
    },
}

_GREENHOUSE_EXPECTED_FILLED = {
    # "Resume" / "Cover Letter" are deliberately excluded from
    # ``fields_filled`` by ``run_field_map_fill``'s pre-existing contract
    # (they get their own "Uploaded resume:" / "Pasted cover letter" notes
    # lines instead) — their coverage is checked below via ``fill_report``.
    "First Name", "Last Name", "Email", "Phone", "LinkedIn URL", "LinkedIn",
    "Location", "Current Location", "City",
    "Current Company", "Current Title",
}
_GREENHOUSE_REQUIRED_EMPTY = {"Are you legally authorized to work in the US?"}


@pytest.mark.parametrize("variant", ["standard", "embed"])
def test_greenhouse_fixture_fill_coverage(page, tmp_path, variant):
    result = _run(
        page, "greenhouse", variant, _GREENHOUSE_JOB, _resume_path(tmp_path),
    )

    assert set(result["fields_filled"]) == _GREENHOUSE_EXPECTED_FILLED
    # The custom work-authorization radio is a required question
    # field_maps.yml has never heard of — scan_required_fields must widen
    # the required-set to catch it on the "standard" variant (embed: the
    # widening pass can't see inside the iframe at all, see
    # ``_expected_required_empty``), and it must be the ONLY gap (proves
    # the widening isn't also swallowing the YAML-declared identity
    # fields).
    expected_required_empty = _expected_required_empty(
        variant, _GREENHOUSE_REQUIRED_EMPTY,
    )
    assert set(result["required_empty"]) == expected_required_empty
    assert result["success"] is (not expected_required_empty)

    report = _fill_report_by_label(result)
    # Phone's fixture markup has NO job_application[phone] name at all —
    # only the intl-tel-input `input[type="tel"]:visible` anchor can reach
    # it. A regression that dropped/reordered that anchor would flip this.
    assert report["Phone"]["matched_selector"] == 'input[type="tel"]:visible'
    # Cover letter has no name/aria-label/placeholder mentioning
    # cover/letter — it ONLY resolves via the scoped bare "textarea"
    # catch-all correctly skipping the earlier mislabeled custom-question
    # textarea and finding the later, properly <label>-associated one.
    assert report["Cover Letter"]["matched_selector"] == "textarea"
    assert report["Cover Letter"]["value_verified"] is True
    assert report["Resume"]["matched_selector"] == (
        'input[type="file"][name="job_application[resume]"]'
    )
    assert report["Resume"]["value_verified"] is True


# ── Lever ────────────────────────────────────────────────────────────────────

_LEVER_JOB = {
    "id": "lv-fixture",
    "form_answers": {
        "first_name": "Priya",
        "last_name": "Natarajan",
        "full_name": "Priya Natarajan",
        "email": "priya.natarajan@example.invalid",
        "phone": "+1-555-0101",
        "linkedin_url": "https://linkedin.example/in/priya-natarajan",
        "current_location": "Remote",
        "current_company": "Prior Field Co",
        "current_title": "Field Technician",
    },
}

_LEVER_EXPECTED_FILLED = {
    # "Resume" / "Cover Letter" excluded — see the Greenhouse set's comment.
    "First Name", "Full Name", "Name", "Email", "Phone",
    "LinkedIn URL", "LinkedIn",
    "Location", "Current Location", "City",
    "Current Company", "Company", "Current Title", "Title",
}
_LEVER_REQUIRED_EMPTY = {"What is your desired start date?"}


@pytest.mark.parametrize("variant", ["standard", "embed"])
def test_lever_fixture_fill_coverage(page, tmp_path, variant):
    full_name = _LEVER_JOB["form_answers"]["full_name"]
    result = _run(
        page, "lever", variant, _LEVER_JOB, _resume_path(tmp_path),
        value_overrides={"Name": full_name, "Full Name": full_name},
    )

    assert set(result["fields_filled"]) == _LEVER_EXPECTED_FILLED
    # Lever's per-card custom question ("cards[<uuid>][field0]") is required
    # and field_maps.yml has no spec for it — DOM widening must catch it on
    # "standard" (embed: see ``_expected_required_empty``).
    expected_required_empty = _expected_required_empty(
        variant, _LEVER_REQUIRED_EMPTY,
    )
    assert set(result["required_empty"]) == expected_required_empty
    assert result["success"] is (not expected_required_empty)

    report = _fill_report_by_label(result)
    # Lever's single name="name" field ends up holding the full name — the
    # LAST spec to fill it (list order: First Name, Full Name, Name) wins.
    assert report["Name"]["matched_selector"] == 'input[name="name"]'
    assert report["Name"]["value_verified"] is True
    # Cover letter direct name="comments" match — complements Greenhouse/
    # Ashby's scoped-fallback coverage with the plain direct-match path.
    assert report["Cover Letter"]["matched_selector"] == 'textarea[name="comments"]'
    assert report["Resume"]["matched_selector"] == 'input[type="file"][name="resume"]'
    assert report["Resume"]["value_verified"] is True
    assert report["Phone"]["matched_selector"] == 'input[type="tel"]:visible'


# ── Ashby ────────────────────────────────────────────────────────────────────

_ASHBY_JOB = {
    "id": "ab-fixture",
    "form_answers": {
        "first_name": "Sam",
        "last_name": "Okafor",
        "email": "sam.okafor@example.invalid",
        "phone": "+1-555-0102",
        "linkedin_url": "https://linkedin.example/in/sam-okafor",
    },
}

_ASHBY_EXPECTED_FILLED = {
    # "Resume" / "Cover Letter" excluded — see the Greenhouse set's comment.
    "First Name", "Last Name", "Email", "Phone",
    "LinkedIn URL", "LinkedIn",
}
_ASHBY_REQUIRED_EMPTY = {"Are you authorized to work in this country?"}


@pytest.mark.parametrize("variant", ["standard", "embed"])
def test_ashby_fixture_fill_coverage(page, tmp_path, variant):
    result = _run(page, "ashby", variant, _ASHBY_JOB, _resume_path(tmp_path))

    assert set(result["fields_filled"]) == _ASHBY_EXPECTED_FILLED
    # The native <select> "Work Authorization" question has no `combobox`
    # (or any) spec in field_maps.yml today — DOM widening must catch it on
    # "standard" (embed: see ``_expected_required_empty``).
    expected_required_empty = _expected_required_empty(
        variant, _ASHBY_REQUIRED_EMPTY,
    )
    assert set(result["required_empty"]) == expected_required_empty
    assert result["success"] is (not expected_required_empty)

    report = _fill_report_by_label(result)
    # Three different label-resolution shapes, each pinned to the ONE
    # selector-chain link that can reach it on this fixture's markup:
    assert report["First Name"]["matched_selector"] == 'label:has-text("First Name") input'
    assert report["Last Name"]["matched_selector"] == 'input[aria-label="Last Name"]'
    assert report["Email"]["matched_selector"] == 'input[placeholder*="Email" i]'
    assert report["Phone"]["matched_selector"] == 'input[type="tel"]:visible'
    # LinkedIn URL has NO label/aria/placeholder at all — only the fuzzy
    # input[name*="linkedin_url"] fallback (Ashby has no canonical name
    # map) reaches it.
    assert report["LinkedIn URL"]["matched_selector"] == 'input[name*="linkedin_url"]'
    # Cover letter: same scoped bare-"textarea" catch-all shape as
    # Greenhouse, proving the fix generalizes across ATSes.
    assert report["Cover Letter"]["matched_selector"] == "textarea"
    assert report["Cover Letter"]["value_verified"] is True
    # Ashby has no canonical file-input name attr — the broadest
    # input[type="file"] candidate (first in the chain) is the one that
    # actually matches this fixture's resume input.
    assert report["Resume"]["matched_selector"] == 'input[type="file"]'
    assert report["Resume"]["value_verified"] is True


# ── Cross-ATS: embed variants actually require the iframe fallback ─────────

@pytest.mark.parametrize("ats", ["greenhouse", "lever", "ashby"])
def test_embed_fixture_top_document_has_no_matching_form_fields(page, ats):
    """Guards the embed fixtures' own premise: the top document must NOT
    contain any of the form's fields directly — every fill in the embed
    variant's coverage test above is only possible because
    ``_iter_frame_locators`` reaches into the iframe. If this assertion
    ever failed, the embed tests above would be silently passing without
    exercising the frame-piercing fallback at all."""
    page.set_content(_load_fixture(ats, "embed"))
    assert page.locator("#application-form").count() == 0
    assert page.locator("iframe").count() == 1
