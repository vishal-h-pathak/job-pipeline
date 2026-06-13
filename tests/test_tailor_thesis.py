"""tests/test_tailor_thesis.py — Session I: thesis.md in every tailor LLM context.

Pins three contracts:

1. The tailor's merged profile string (`prompts.load_profile`) splices
   thesis.md FIRST with the same canonical wins-on-conflict banner the
   hunt scorer uses.
2. The archetype router's prompt includes the bannered thesis.
3. `_shared.md` carries the thesis's binding tone note (never "generic
   ML researcher", never plain "embedded engineer").

The tailor subtree uses bare intra-subtree imports (`from prompts import
...`); importing `jobpipe.tailor.pipeline` first bootstraps sys.path the
same way the console scripts do.
"""

from __future__ import annotations

import pytest

from jobpipe.tailor import pipeline  # noqa: F401 — sys.path bootstrap

import prompts as tailor_prompts
from tailor import archetype


THESIS_BANNER_PREFIX = "========== thesis.md (CANONICAL"
BINDING_FRAME = "building brains in hardware and software"


@pytest.fixture
def fresh_profile_cache(monkeypatch):
    """Reset the module-level profile cache so each test sees a clean load."""
    monkeypatch.setattr(tailor_prompts, "_PROFILE_CACHE", None)
    yield
    monkeypatch.setattr(tailor_prompts, "_PROFILE_CACHE", None)


def _all_prompt_text(kwargs: dict) -> str:
    """Flatten an Anthropic messages.create kwargs dict into one string.

    Handles both the plain-string prompt shape and the cached
    system-blocks shape so this test survives the Session I caching
    refactor without rewriting.
    """
    chunks: list[str] = []
    system = kwargs.get("system")
    if isinstance(system, str):
        chunks.append(system)
    elif isinstance(system, list):
        for block in system:
            chunks.append(block.get("text", "") if isinstance(block, dict) else str(block))
    for msg in kwargs.get("messages", []):
        content = msg.get("content")
        if isinstance(content, str):
            chunks.append(content)
        elif isinstance(content, list):
            for block in content:
                chunks.append(block.get("text", "") if isinstance(block, dict) else str(block))
    return "\n".join(chunks)


def test_thesis_section_is_bannered():
    section = tailor_prompts.thesis_section()
    assert section.startswith(THESIS_BANNER_PREFIX)
    assert "thesis.md\nwins" in section or "thesis.md wins" in section.replace("\n", " ")
    # Real thesis content made it through, not just the banner.
    assert "Hunting Thesis" in section


def test_load_profile_puts_thesis_first(fresh_profile_cache):
    profile = tailor_prompts.load_profile()
    assert profile.startswith(THESIS_BANNER_PREFIX), (
        "thesis.md must be the FIRST profile document in the tailor's "
        "merged profile string"
    )
    # The structured profile still follows it.
    assert "========== profile.yml ==========" in profile
    assert profile.index(THESIS_BANNER_PREFIX) < profile.index(
        "========== profile.yml =========="
    )


def test_classifier_prompt_includes_thesis(monkeypatch):
    sent: dict = {}

    class _FakeBlock:
        text = (
            '{"archetype": "tier_3_mission_ml", '
            '"confidence": 0.5, "reasoning": "stub"}'
        )

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
        {"title": "Test Role", "company": "TestCo", "description": "A JD."}
    )
    assert result["archetype"] in archetype.archetype_keys()
    prompt_text = _all_prompt_text(sent)
    assert THESIS_BANNER_PREFIX in prompt_text
    assert "Hunting Thesis" in prompt_text


def test_shared_rules_carry_binding_tone_note():
    shared = tailor_prompts._shared()
    assert BINDING_FRAME in shared
    assert "ML researcher" in shared
    assert "embedded engineer" in shared
