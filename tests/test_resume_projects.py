"""tests/test_resume_projects.py — archetype-conditional Projects section.

Covers the canonical-source overhaul:

- BASE_RESUME is assembled from resume_source.yml: professional experience
  only (GTRI then Rain), no generic "Personal Projects" employer.
- projects_for_archetype() returns the right conditional bank subset.
- _render_latex emits a Projects section ONLY when the tailored dict carries
  a non-empty ``projects`` list (no empty header otherwise).
- The one-page trim drops personal-project content BEFORE professional
  experience, so the professional spine is never sacrificed to fit a project.

All pure string assembly — no live LLM, no real pdflatex.
"""

from __future__ import annotations

from jobpipe.tailor import pipeline  # noqa: F401 — sys.path bootstrap
from tailor import latex_resume as latex_mod


# ── BASE_RESUME assembled from the canonical source ────────────────────────


def test_base_resume_is_professional_only():
    orgs = [e["org"] for e in latex_mod.BASE_RESUME["experience"]]
    assert "Georgia Tech Research Institute" in orgs[0]
    assert any("Rain" in o for o in orgs)
    # The old generic block is gone — no personal-projects employer entry.
    assert not any("Personal Projects" in o for o in orgs)
    # GTRI programs flowed through as resume "projects".
    gtri = latex_mod.BASE_RESUME["experience"][0]
    assert any("SPARSE" in (p.get("name") or "") for p in gtri["projects"])


# ── projects_for_archetype ─────────────────────────────────────────────────


def test_projects_for_agentic_returns_agentic_bank():
    projs = latex_mod.projects_for_archetype("tier_1_5_agentic_builder")
    keys = [p["key"] for p in projs]
    assert "job-pipeline" in keys
    assert "papercuts" in keys


def test_projects_for_brain_lanes_returns_cellular_gaits():
    for lane in ("tier_1a_compneuro", "tier_1b_neuromorphic", "tier_1c_bci"):
        projs = latex_mod.projects_for_archetype(lane)
        assert [p["key"] for p in projs] == ["cellular-gaits"]


def test_projects_for_se_and_mission_are_empty():
    assert latex_mod.projects_for_archetype("tier_2_ai_se") == []
    assert latex_mod.projects_for_archetype("tier_3_mission_ml") == []


def test_projects_for_unknown_archetype_is_empty():
    assert latex_mod.projects_for_archetype("nonsense") == []
    assert latex_mod.projects_for_archetype("") == []


# ── _render_latex: conditional Projects section ────────────────────────────


def _professional_only_tailored() -> dict:
    return {
        "skills": {"A": "a, b"},
        "experience": [
            {
                "org": "GTRI",
                "title": "Engineer",
                "location": "Atlanta",
                "period": "2021--Present",
                "projects": [
                    {"name": "SPARSE", "period": "2021", "bullets": ["did a thing"]},
                ],
            },
        ],
    }


def test_render_omits_projects_section_when_absent():
    latex = latex_mod._render_latex(_professional_only_tailored(), "classic")
    assert "\\section{Projects}" not in latex


def test_render_omits_projects_section_when_empty_list():
    t = _professional_only_tailored()
    t["projects"] = []
    latex = latex_mod._render_latex(t, "classic")
    assert "\\section{Projects}" not in latex


def test_render_emits_projects_section_when_present():
    t = _professional_only_tailored()
    t["projects"] = [
        {"name": "papercuts", "description": "live members-only book club"},
    ]
    latex = latex_mod._render_latex(t, "classic")
    assert "\\section{Projects}" in latex
    assert "papercuts" in latex
    assert "live members-only book club" in latex


# ── trim: personal projects before professional experience ─────────────────


def _tailored_with_projects() -> dict:
    long = "x" * 120
    return {
        "skills": {"A": "a, b"},
        "experience": [
            {
                "org": "GTRI",
                "title": "Engineer",
                "location": "Atlanta",
                "period": "2021--Present",
                "projects": [
                    {"name": "SPARSE", "period": "2021", "bullets": [long] * 4},
                ],
            },
        ],
        "projects": [
            {"name": "job-pipeline", "description": long},
            {"name": "papercuts", "description": long},
        ],
    }


def _professional_bullets(t: dict) -> int:
    return sum(
        len(p.get("bullets") or [])
        for e in t.get("experience", [])
        for p in e.get("projects", [])
    )


def test_trim_drops_personal_project_before_professional_bullet():
    t = _tailored_with_projects()
    prof_before = _professional_bullets(t)

    # First trim removes a personal project, leaving professional untouched.
    assert latex_mod._trim_one_unit(t) is True
    assert len(t["projects"]) == 1
    assert _professional_bullets(t) == prof_before

    # Second trim removes the last personal project — still no professional cut.
    assert latex_mod._trim_one_unit(t) is True
    assert t["projects"] == []
    assert _professional_bullets(t) == prof_before

    # Only once personal projects are exhausted does professional content trim.
    assert latex_mod._trim_one_unit(t) is True
    assert _professional_bullets(t) == prof_before - 1


def test_fit_to_one_page_trims_projects_first():
    t = _tailored_with_projects()
    prof_before = _professional_bullets(t)
    page_seq = [2, 2, 1]

    def fake_compile_and_count(latex):
        return (True, page_seq.pop(0), b"%PDF-fake", "")

    result = latex_mod._fit_to_one_page(t, "modern", fake_compile_and_count)
    final = result["tailored_data"]
    assert result["pages"] == 1
    # Two trims happened; both came off personal projects, professional spine
    # fully intact.
    assert final["projects"] == []
    assert _professional_bullets(final) == prof_before


# ── generate_tailored_latex: archetype-conditional bank reaches the prompt ──


def _capture_prompt_kwargs(monkeypatch) -> dict:
    """Patch generate_tailored_latex's collaborators and capture the kwargs
    handed to the prompt loader, so we can assert which project bank reached
    the model without a live LLM or pdflatex."""
    captured: dict = {}

    def fake_load_task_prompt(*names, **vars):
        captured.update(vars)
        return "PROMPT"

    # Minimal valid tailored JSON; projects echoed straight through to render.
    def fake_complete(**kwargs):
        return (
            '{"skills": {"A": "a, b"}, "experience": ['
            '{"org": "GTRI", "title": "Engineer", "location": "Atlanta", '
            '"period": "2021--Present", "projects": ['
            '{"name": "SPARSE", "period": "2021", "bullets": ["did a thing"]}]}], '
            '"projects": [{"name": "papercuts", "description": "shipped book club"}]}'
        )

    monkeypatch.setattr(latex_mod, "load_task_prompt", fake_load_task_prompt)
    monkeypatch.setattr(latex_mod.llm, "complete", fake_complete)
    return captured


def test_generate_agentic_passes_project_bank(monkeypatch):
    captured = _capture_prompt_kwargs(monkeypatch)
    job = {
        "title": "Forward-deployed AI engineer",
        "company": "TestCo",
        "description": "build agents",
        "_archetype": {"archetype": "tier_1_5_agentic_builder"},
    }
    result = latex_mod.generate_tailored_latex(job, {})
    # The agentic bank reached the prompt...
    assert "job-pipeline" in captured["project_bank_block"]
    # ...and the LLM-returned project rendered into a Projects section.
    assert "\\section{Projects}" in result["latex_source"]


def test_generate_tier2_passes_no_project_bank(monkeypatch):
    captured = _capture_prompt_kwargs(monkeypatch)
    job = {
        "title": "Sales engineer",
        "company": "TestCo",
        "description": "customer-facing AI",
        "_archetype": {"archetype": "tier_2_ai_se"},
    }
    latex_mod.generate_tailored_latex(job, {})
    # tier_2 is all-professional — the prompt is told there is no bank.
    assert "none" in captured["project_bank_block"].lower()
    assert "ALL-PROFESSIONAL" in captured["project_bank_block"]
