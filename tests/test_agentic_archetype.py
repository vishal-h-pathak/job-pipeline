"""tests/test_agentic_archetype.py — Session I: tier_1_5_agentic_builder (J-4).

Pins the new archetype's wiring:

- defined in profile.yml::archetypes and loadable
- framing carries the flagship proof artifact (the job pipeline,
  described concretely) and the neuromorphic differentiator
- tier 1.5 routes to it deterministically (no LLM call)
- the classifier prompt carries the routing rule for JD-central cases
- every prompt that branches on tier knows the 1.5 lane
"""

from __future__ import annotations

from pathlib import Path

import pytest

from jobpipe.tailor import pipeline  # noqa: F401 — sys.path bootstrap

from tailor import archetype

AGENTIC_KEY = "tier_1_5_agentic_builder"
PROMPTS_DIR = (
    Path(__file__).resolve().parent.parent / "jobpipe" / "tailor" / "prompts"
)


def test_agentic_archetype_is_configured():
    assert AGENTIC_KEY in archetype.archetype_keys()
    resolved, cfg = archetype.archetype_config(AGENTIC_KEY)
    assert resolved == AGENTIC_KEY
    assert cfg.get("label")


def test_agentic_block_carries_flagship_and_differentiator():
    block = archetype.render_archetype_block(AGENTIC_KEY)
    # Flagship artifact described concretely, with its load-bearing parts.
    assert "stop-at-submit human gate" in block
    assert "fit/legitimacy" in block
    assert "audit trail" in block
    assert "closed-loop pattern analysis" in block
    # The anti-vagueness rule itself.
    assert 'never as "an AI project"' in block
    # Secondary differentiator: neuromorphic depth.
    assert "neurons in silicon" in block


def test_tier_1_5_routes_deterministically(monkeypatch):
    """A tier-1.5 job must route to the agentic lane without an LLM call."""

    def _boom():
        raise AssertionError("classifier must not call the LLM for tier 1.5")

    monkeypatch.setattr(archetype, "_client_lazy", _boom)
    for tier in ("1.5", 1.5):
        result = archetype.classify_archetype(
            {"title": "Agent Engineer", "company": "X", "description": "...",
             "tier": tier}
        )
        assert result["archetype"] == AGENTIC_KEY
        assert result["confidence"] == 1.0


def test_non_1_5_tiers_still_use_the_llm(monkeypatch):
    sent: dict = {}

    class _FakeBlock:
        text = '{"archetype": "tier_1a_compneuro", "confidence": 0.9, "reasoning": "x"}'

    class _FakeResp:
        content = [_FakeBlock()]

    class _FakeMessages:
        def create(self, **kwargs):
            sent.update(kwargs)
            return _FakeResp()

    class _FakeClient:
        messages = _FakeMessages()

    monkeypatch.setattr(archetype, "_client_lazy", lambda: _FakeClient())
    result = archetype.classify_archetype(
        {"title": "Neuro Engineer", "company": "X", "description": "brains",
         "tier": 1}
    )
    assert result["archetype"] == "tier_1a_compneuro"
    assert sent, "tier 1 must still go through the LLM classifier"


def test_classifier_prompt_carries_routing_rule():
    body = (PROMPTS_DIR / "classify_archetype.md").read_text(encoding="utf-8")
    assert AGENTIC_KEY in body
    assert "forward-deployed" in body
    assert "Tier: {tier}" in body


@pytest.mark.parametrize(
    "prompt_file",
    ["tailor_resume.md", "tailor_cover_letter.md", "form_answers.md"],
)
def test_tier_branching_prompts_know_the_1_5_lane(prompt_file):
    body = (PROMPTS_DIR / prompt_file).read_text(encoding="utf-8")
    assert "1.5" in body, f"{prompt_file} tier legend must include 1.5"
    assert "TIER 1.5" in body, f"{prompt_file} must carry tier-1.5 framing rules"
