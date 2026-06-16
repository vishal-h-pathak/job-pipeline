"""tests/test_latex_onepage.py — one-page guarantee + ATS-safe styles.

Covers the deterministic trim loop that guarantees a single-page resume
regardless of LLM output, plus the archetype→style selection map. All
mocked — no live LLM, no real pdflatex (the compile+count step is
injected).
"""

from __future__ import annotations

import pytest

from jobpipe.tailor import pipeline  # noqa: F401 — sys.path bootstrap
from tailor import latex_resume as latex_mod


# ── Fixtures ──────────────────────────────────────────────────────────────


def _fat_tailored() -> dict:
    """A tailored dict that overflows one page: two employers, several
    projects each with multiple long bullets."""
    long = "x" * 120
    return {
        "skills": {"A": "a, b, c", "B": "d, e, f"},
        "experience": [
            {
                "org": "GTRI",
                "title": "Engineer",
                "location": "Atlanta",
                "period": "2021--Present",
                "projects": [
                    {"name": "SPARSE", "period": "2021", "bullets": [long] * 4},
                    {"name": "360-SA", "period": "2023", "bullets": [long] * 4},
                ],
            },
            {
                "org": "Rain",
                "title": "Intern",
                "location": "FL",
                "period": "2017--2018",
                "projects": [
                    {"name": None, "period": None, "bullets": [long] * 3},
                ],
            },
        ],
    }


def _total_bullets(tailored: dict) -> int:
    return sum(
        len(p.get("bullets") or [])
        for e in tailored.get("experience", [])
        for p in e.get("projects", [])
    )


def _total_projects(tailored: dict) -> int:
    return sum(len(e.get("projects", [])) for e in tailored.get("experience", []))


# ── _pdf_page_count ───────────────────────────────────────────────────────


def test_pdf_page_count_parses_stdout_single():
    out = "...\nOutput written on resume_TestCo.pdf (1 page, 12345 bytes).\n"
    assert latex_mod._pdf_page_count(out, "/nonexistent.pdf") == 1


def test_pdf_page_count_parses_stdout_multi():
    out = "Output written on /tmp/x/resume.pdf (3 pages, 99 bytes)."
    assert latex_mod._pdf_page_count(out, "/nonexistent.pdf") == 3


def test_pdf_page_count_none_when_unparseable():
    assert latex_mod._pdf_page_count("no marker here", "/nonexistent.pdf") is None


# ── _select_style ─────────────────────────────────────────────────────────


@pytest.mark.parametrize(
    "archetype,expected",
    [
        ("tier_1a_compneuro", "classic"),
        ("tier_1b_neuromorphic", "classic"),
        ("tier_1c_bci", "classic"),
        ("tier_3_mission_ml", "classic"),
        ("tier_1_5_agentic_builder", "modern"),
        ("tier_2_ai_se", "modern"),
    ],
)
def test_select_style_maps_archetype(archetype, expected):
    assert latex_mod._select_style(archetype) == expected


def test_select_style_falls_back_to_classic():
    assert latex_mod._select_style("unknown_archetype") == "classic"
    assert latex_mod._select_style("") == "classic"


# ── STYLES dict ───────────────────────────────────────────────────────────


def test_styles_has_three_ats_safe_variants():
    assert set(latex_mod.STYLES) == {"classic", "modern", "compact"}
    for key, template in latex_mod.STYLES.items():
        # ATS safety: no graphics/multicolumn/colored text blocks.
        assert "\\pagestyle{empty}" in template
        for banned in ("graphicx", "includegraphics", "multicol", "\\columnbreak"):
            assert banned not in template, f"{key} contains {banned}"
        # Same placeholders so the shared renderer works for every style.
        for ph in ("<<NAME>>", "<<EDU_AND_SKILLS>>", "<<EXPERIENCE_BLOCKS>>"):
            assert ph in template


# ── _trim_one_unit ────────────────────────────────────────────────────────


def test_trim_drops_bullet_from_longest_entry():
    t = _fat_tailored()
    before = _total_bullets(t)
    projects_before = _total_projects(t)
    assert latex_mod._trim_one_unit(t) is True
    # One bullet gone, no whole entry removed.
    assert _total_bullets(t) == before - 1
    assert _total_projects(t) == projects_before
    # It came off a 4-bullet GTRI project (the longest), not the 3-bullet Rain.
    rain = t["experience"][1]["projects"][0]
    assert len(rain["bullets"]) == 3
    gtri_bullets = sum(len(p["bullets"]) for p in t["experience"][0]["projects"])
    assert gtri_bullets == 7


def test_trim_drops_whole_entry_only_at_floor():
    # Every project already at the 1-bullet floor.
    t = {
        "experience": [
            {"org": "A", "projects": [
                {"name": "p1", "bullets": ["one"]},
                {"name": "p2", "bullets": ["two"]},
            ]},
        ]
    }
    projects_before = _total_projects(t)
    assert latex_mod._trim_one_unit(t) is True
    # A whole entry was dropped (can't trim bullets below floor).
    assert _total_projects(t) == projects_before - 1


def test_trim_returns_false_when_nothing_left():
    t = {"experience": [{"org": "A", "projects": [{"name": "p", "bullets": ["only"]}]}]}
    assert latex_mod._trim_one_unit(t) is False


# ── _fit_to_one_page ──────────────────────────────────────────────────────


def test_fit_trims_until_one_page_bullets_before_entries():
    t = _fat_tailored()
    bullets_before = _total_bullets(t)
    projects_before = _total_projects(t)

    # Compile reports 2 pages twice, then 1 page.
    page_seq = [2, 2, 1]

    def fake_compile_and_count(latex):
        return (True, page_seq.pop(0), b"%PDF-fake", "")

    result = latex_mod._fit_to_one_page(t, "classic", fake_compile_and_count)

    assert result["pages"] == 1
    assert result["compile_success"] is True
    final = result["tailored_data"]
    # Two trims happened, both removed bullets (entries had >1 bullet),
    # so no whole project/employer was dropped.
    assert _total_bullets(final) == bullets_before - 2
    assert _total_projects(final) == projects_before
    # Caller's dict was not mutated in place.
    assert _total_bullets(t) == bullets_before


def test_fit_stops_at_iteration_cap():
    t = _fat_tailored()
    # Always 2 pages — the cap must stop the loop.
    def always_two(latex):
        return (True, 2, b"%PDF-fake", "")

    result = latex_mod._fit_to_one_page(t, "classic", always_two, max_iters=3)
    assert result["trim_iterations"] == 3


def test_fit_pdflatex_absent_returns_gracefully():
    t = _fat_tailored()
    bullets_before = _total_bullets(t)

    def no_latex(latex):
        return (False, None, None, "pdflatex not found — LaTeX not installed")

    result = latex_mod._fit_to_one_page(t, "classic", no_latex)
    assert result["compile_success"] is False
    assert "pdflatex not found" in result["compile_log"]
    # No trimming attempted when we can't even compile once.
    assert _total_bullets(result["tailored_data"]) == bullets_before
    assert "latex_source" in result
