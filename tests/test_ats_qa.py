"""P2 (callback feedback loop) — jobpipe.tailor.tailor.ats_qa.

Post-tailor ATS keyword gate + humanizer. Fully mocked: ``llm.complete``
is patched to a fake, so no live Anthropic call. Covers JSON parsing +
normalization, the never-fabricate guard over ``robotic_bullets``
(drops rewrites that invent a bullet or drop a real number), and the
"never raise, never block ready_for_review" contract on a malformed
model response.
"""

from __future__ import annotations

import json

from jobpipe.tailor import pipeline  # noqa: F401 — sys.path bootstrap for bare `prompts` import
from jobpipe.tailor.tailor import ats_qa


_JOB = {"title": "ML Engineer", "company": "Acme", "description": "Build agents in Python"}
_RESUME_TEXT = (
    "Built agentic pipelines in Python.\n"
    "- Cut p95 latency from 2.1s to 380ms across 3 services.\n"
    "- Shipped a resume tailoring pipeline used by 40 applicants.\n"
)


def _patch_llm(monkeypatch, response_text: str):
    captured = {}

    def fake_complete(*, system, prompt, model, max_tokens):
        captured.update(system=system, prompt=prompt, model=model, max_tokens=max_tokens)
        return response_text

    monkeypatch.setattr(ats_qa.llm, "complete", fake_complete)
    monkeypatch.setattr(ats_qa, "cached_system_blocks", lambda: ["SYSTEM"])
    return captured


# ── Happy path ──────────────────────────────────────────────────────────


def test_run_ats_qa_parses_and_normalizes(monkeypatch):
    body = json.dumps({
        "top_keywords": ["python", "agents", "latency"],
        "missing": ["kubernetes"],
        "ats_score": 150,  # out of range — must clamp
        "highest_impact_fix": "Add a Kubernetes bullet.",
        "robotic_bullets": [],
    })
    _patch_llm(monkeypatch, body)

    result = ats_qa.run_ats_qa(_JOB, _RESUME_TEXT)

    assert result["top_keywords"] == ["python", "agents", "latency"]
    assert result["missing"] == ["kubernetes"]
    assert result["ats_score"] == 100  # clamped to [0, 100]
    assert result["highest_impact_fix"] == "Add a Kubernetes bullet."
    assert result["robotic_bullets"] == []


def test_run_ats_qa_caps_top_keywords_at_15(monkeypatch):
    body = json.dumps({
        "top_keywords": [f"kw{i}" for i in range(20)],
        "missing": [], "ats_score": 50, "highest_impact_fix": "x",
        "robotic_bullets": [],
    })
    _patch_llm(monkeypatch, body)
    result = ats_qa.run_ats_qa(_JOB, _RESUME_TEXT)
    assert len(result["top_keywords"]) == 15


def test_run_ats_qa_strips_code_fences(monkeypatch):
    body = "```json\n" + json.dumps({
        "top_keywords": ["python"], "missing": [], "ats_score": 60,
        "highest_impact_fix": "x", "robotic_bullets": [],
    }) + "\n```"
    _patch_llm(monkeypatch, body)
    result = ats_qa.run_ats_qa(_JOB, _RESUME_TEXT)
    assert result["ats_score"] == 60


# ── Never-fabricate guard on robotic_bullets ───────────────────────────────


def test_never_fabricate_guard_keeps_valid_rewrite_with_matching_numbers():
    resume_text = _RESUME_TEXT
    bullets = [{
        "bullet": "Cut p95 latency from 2.1s to 380ms across 3 services.",
        "humanized_rewrite": "Dropped p95 latency 2.1s to 380ms on 3 services.",
    }]
    validated = ats_qa._validate_robotic_bullets(resume_text, bullets)
    assert validated == bullets


def test_never_fabricate_guard_drops_bullet_not_in_resume():
    """The model invented a bullet that isn't actually in the resume —
    never trust a 'fix' for text that doesn't exist."""
    bullets = [{
        "bullet": "Led a team of 12 engineers to ship a rocket.",
        "humanized_rewrite": "Managed 12 engineers shipping a rocket.",
    }]
    validated = ats_qa._validate_robotic_bullets(_RESUME_TEXT, bullets)
    assert validated == []


def test_never_fabricate_guard_drops_rewrite_that_loses_a_number():
    """The rewrite dropped the '3 services' and '380ms' figures — a
    real metric silently vanished, so the guard rejects it even though
    the original bullet does exist in the resume."""
    bullets = [{
        "bullet": "Cut p95 latency from 2.1s to 380ms across 3 services.",
        "humanized_rewrite": "Improved latency significantly across services.",
    }]
    validated = ats_qa._validate_robotic_bullets(_RESUME_TEXT, bullets)
    assert validated == []


def test_never_fabricate_guard_drops_malformed_entries():
    bullets = [
        {"bullet": "", "humanized_rewrite": "something"},
        {"humanized_rewrite": "no bullet key"},
        "not even a dict",
        None,
    ]
    validated = ats_qa._validate_robotic_bullets(_RESUME_TEXT, bullets)
    assert validated == []


def test_run_ats_qa_applies_never_fabricate_guard_end_to_end(monkeypatch):
    body = json.dumps({
        "top_keywords": [], "missing": [], "ats_score": 40,
        "highest_impact_fix": "x",
        "robotic_bullets": [
            {
                "bullet": "Cut p95 latency from 2.1s to 380ms across 3 services.",
                "humanized_rewrite": "Dropped p95 latency 2.1s to 380ms on 3 services.",
            },
            {
                "bullet": "Invented bullet that is not in the resume.",
                "humanized_rewrite": "Rewrite of a fabricated bullet.",
            },
        ],
    })
    _patch_llm(monkeypatch, body)
    result = ats_qa.run_ats_qa(_JOB, _RESUME_TEXT)
    assert len(result["robotic_bullets"]) == 1
    assert "2.1s" in result["robotic_bullets"][0]["bullet"]


# ── Never blocks the caller ─────────────────────────────────────────────


def test_run_ats_qa_never_raises_on_malformed_llm_response(monkeypatch):
    monkeypatch.setattr(ats_qa.llm, "complete", lambda **_: "not json at all")
    monkeypatch.setattr(ats_qa, "cached_system_blocks", lambda: ["SYSTEM"])

    result = ats_qa.run_ats_qa(_JOB, _RESUME_TEXT)

    assert result["top_keywords"] == []
    assert result["missing"] == []
    assert result["ats_score"] == 0
    assert result["highest_impact_fix"] == ""
    assert result["robotic_bullets"] == []


def test_run_ats_qa_never_raises_when_llm_call_throws(monkeypatch):
    def _boom(**_):
        raise RuntimeError("network down")

    monkeypatch.setattr(ats_qa.llm, "complete", _boom)
    monkeypatch.setattr(ats_qa, "cached_system_blocks", lambda: ["SYSTEM"])

    result = ats_qa.run_ats_qa(_JOB, _RESUME_TEXT)
    assert result["ats_score"] == 0


# ── resume_text_from_tailored flattening ───────────────────────────────────


def test_resume_text_from_tailored_flattens_summary_skills_and_bullets():
    tailored = {
        "tailored_summary": "Agentic builder.",
        "skills": {"Languages": ["Python", "C++"]},
        "experience": [
            {
                "title": "Engineer", "org": "Acme",
                "projects": [{"bullets": ["Did a thing.", "Did another thing."]}],
            }
        ],
    }
    text = ats_qa.resume_text_from_tailored(tailored)
    assert "Agentic builder." in text
    assert "Python" in text
    assert "Did a thing." in text
    assert "Did another thing." in text


def test_resume_text_from_tailored_handles_empty_dict():
    assert ats_qa.resume_text_from_tailored({}) == ""
    assert ats_qa.resume_text_from_tailored(None) == ""
