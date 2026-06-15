"""tests/test_hunt_scorer.py — the hunt scorer routes Claude calls through
the shared credits-first → Max-OAuth-fallback helper (jobpipe.shared.llm),
not a bare anthropic.Anthropic client.

Fully mocked: ``llm.complete`` is patched to a fake, so no live Anthropic
call and no API credits are spent. Asserts the scorer hands the helper its
system + user prompt and the configured model, then parses the JSON reply.
"""

from __future__ import annotations

from jobpipe.hunt import scorer


def test_score_job_routes_through_shared_llm(monkeypatch):
    captured = {}

    def fake_complete(*, system, prompt, model, max_tokens):
        captured.update(
            system=system, prompt=prompt, model=model, max_tokens=max_tokens
        )
        return (
            '{"score": 8, "tier": 1, "reasoning": "great fit", '
            '"recommended_action": "notify", "legitimacy": "high_confidence"}'
        )

    monkeypatch.setattr(scorer.llm, "complete", fake_complete)
    monkeypatch.setattr(scorer, "build_profile_prompt_string", lambda: "PROFILE-CTX")
    monkeypatch.setattr(scorer, "_system", lambda: "SYSTEM-PROMPT")

    result = scorer.score_job("ML Engineer", "Acme", "build models", "Remote")

    # Parsed + normalized correctly
    assert result["score"] == 8
    assert result["tier"] == 1
    assert result["legitimacy"] == "high_confidence"

    # Routed through the shared helper with the scorer's model + prompts
    assert captured["model"] == scorer.MODEL
    assert captured["system"] == "SYSTEM-PROMPT"
    assert "ML Engineer" in captured["prompt"]
    assert "Acme" in captured["prompt"]
    assert "PROFILE-CTX" in captured["prompt"]


def test_score_job_does_not_build_a_bare_anthropic_client(monkeypatch):
    """Regression for the $0-credits failure: the scorer must not call the
    Anthropic SDK directly — all auth goes through llm.complete."""
    monkeypatch.setattr(
        scorer.llm, "complete",
        lambda **_: '{"score": 5, "tier": 2, "reasoning": "ok"}',
    )
    monkeypatch.setattr(scorer, "build_profile_prompt_string", lambda: "P")
    monkeypatch.setattr(scorer, "_system", lambda: "S")

    def _boom(*a, **k):
        raise AssertionError("scorer built a bare Anthropic client")

    # If a stray anthropic.Anthropic(...) construction survives, fail loudly.
    import anthropic
    monkeypatch.setattr(anthropic, "Anthropic", _boom)

    result = scorer.score_job("Eng", "Co", "desc", "Remote")
    assert result["score"] == 5
