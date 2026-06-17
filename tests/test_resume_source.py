"""tests/test_resume_source.py — canonical resume source (resume_source.yml).

Pins the structured transcription of RESUME_CANONICAL_SOURCE.md and the
loader that reads it:

- the four top-level sections load with the expected shape
- professional_experience is the spine (GTRI first w/ programs, then Rain)
- project_bank carries the honest one-liners + archetype tags
- archetype_projects maps each lane to its project keys (or empty)

Reads the real repo ``profile/resume_source.yml`` (no env override) so the
transcription itself is under test, not a fixture.
"""

from __future__ import annotations

from jobpipe import profile_loader


def test_load_resume_source_has_four_sections():
    src = profile_loader.load_resume_source()
    assert isinstance(src, dict)
    for key in (
        "professional_experience",
        "project_bank",
        "archetype_projects",
        "honesty_constraints",
    ):
        assert key in src, f"missing section {key!r}"


def test_professional_experience_is_gtri_then_rain():
    src = profile_loader.load_resume_source()
    orgs = src["professional_experience"]
    assert "Georgia Tech" in orgs[0]["org"]
    assert "Rain" in orgs[-1]["org"]

    # GTRI carries its programs as ordered entries; SPARSE leads.
    gtri_programs = orgs[0]["programs"]
    assert "SPARSE" in gtri_programs[0]["name"]
    assert all({"name", "period", "bullets"} <= set(p) for p in gtri_programs)

    # Rain kept verbatim — the employee #5 bullet survives.
    rain_bullets = orgs[-1]["programs"][0]["bullets"]
    assert any("employee #5" in b for b in rain_bullets)


def test_project_bank_carries_keys_and_honest_status():
    src = profile_loader.load_resume_source()
    bank = {p["key"]: p for p in src["project_bank"]}
    for key in ("job-pipeline", "meridian", "papercuts", "cellular-gaits"):
        assert key in bank, f"missing project {key!r}"

    # cellular-gaits is the brain-lane artifact, tagged for all three lanes.
    assert set(bank["cellular-gaits"]["archetypes"]) == {
        "tier_1a_compneuro",
        "tier_1b_neuromorphic",
        "tier_1c_bci",
    }
    # Honest status is part of the one-liner.
    assert "paper trading" in bank["meridian"]["one_liner"].lower()


def test_archetype_projects_map():
    src = profile_loader.load_resume_source()
    m = src["archetype_projects"]

    # Agentic lane → the agentic bank (job-pipeline + papercuts at minimum).
    assert "job-pipeline" in m["tier_1_5_agentic_builder"]
    assert "papercuts" in m["tier_1_5_agentic_builder"]

    # Brain lanes → cellular-gaits only.
    assert m["tier_1a_compneuro"] == ["cellular-gaits"]
    assert m["tier_1b_neuromorphic"] == ["cellular-gaits"]
    assert m["tier_1c_bci"] == ["cellular-gaits"]

    # SE + mission ML → no projects (all-professional).
    assert m["tier_2_ai_se"] == []
    assert m["tier_3_mission_ml"] == []


def test_honesty_constraints_present():
    src = profile_loader.load_resume_source()
    constraints = src["honesty_constraints"]
    assert constraints
    assert any("paper trading" in c.lower() for c in constraints)
