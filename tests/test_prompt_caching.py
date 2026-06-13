"""tests/test_prompt_caching.py — Session I: prompt caching across tailor calls.

Every tailor LLM call must send the identical static prefix
(_shared.md rules + thesis-first candidate profile + voice profile) as
``system`` content blocks with ``cache_control`` on the final block,
and keep only per-job content in the (uncached) user turn. Identical
prefixes are what make the cache hit across the six call sites.
"""

from __future__ import annotations

import pytest

from jobpipe.tailor import pipeline  # noqa: F401 — sys.path bootstrap

import prompts as tailor_prompts
from interview_prep import generator as stories_mod
from tailor import archetype as archetype_mod
from tailor import cover_letter as cl_mod
from tailor import form_answers as fa_mod
from tailor import resume as resume_mod

_ARCHETYPE_STUB = {"archetype": "tier_1a_compneuro", "confidence": 0.9, "reasoning": "s"}


def _job() -> dict:
    return {
        "id": "cache-test",
        "title": "Research Engineer",
        "company": "TestCo",
        "description": "Build things.",
        "tier": 1,
        "_archetype": dict(_ARCHETYPE_STUB),
    }


class _FakeBlock:
    def __init__(self, text: str):
        self.text = text


class _FakeClient:
    def __init__(self, response_text: str):
        self.sent: list[dict] = []
        outer = self

        class _Messages:
            def create(self, **kwargs):
                outer.sent.append(kwargs)

                class _Resp:
                    content = [_FakeBlock(response_text)]

                return _Resp()

        self.messages = _Messages()


def test_cached_system_blocks_shape():
    blocks = tailor_prompts.cached_system_blocks()
    assert len(blocks) == 1
    block = blocks[0]
    assert block["type"] == "text"
    assert block["cache_control"] == {"type": "ephemeral"}
    text = block["text"]
    # Global rules + thesis-first profile + voice profile, in that order.
    assert "Global Rules" in text
    assert "========== thesis.md (CANONICAL" in text
    assert "CANDIDATE PROFILE" in text
    assert "VOICE PROFILE" in text
    assert text.index("Global Rules") < text.index("thesis.md (CANONICAL")


def test_cached_system_blocks_is_stable():
    """Same object every call — the byte-identical prefix the cache needs."""
    assert tailor_prompts.cached_system_blocks() is tailor_prompts.cached_system_blocks()


def _assert_cached_call(kwargs: dict):
    assert kwargs["system"] is tailor_prompts.cached_system_blocks()
    user_text = kwargs["messages"][0]["content"]
    # Per-job turn must not duplicate the cached prefix content.
    assert "Global Rules" not in user_text
    assert "========== profile.yml" not in user_text
    assert "{profile}" not in user_text and "{voice_profile}" not in user_text


def test_tailor_resume_uses_cached_prefix(monkeypatch):
    fake = _FakeClient('{"tailored_summary": "x", "emphasis_areas": []}')
    monkeypatch.setattr(resume_mod, "client", fake)
    resume_mod.tailor_resume(_job())
    _assert_cached_call(fake.sent[0])


def test_cover_letter_uses_cached_prefix(monkeypatch):
    fake = _FakeClient("body")
    monkeypatch.setattr(cl_mod, "client", fake)
    cl_mod.generate_cover_letter(_job())
    _assert_cached_call(fake.sent[0])


def test_latex_resume_uses_cached_prefix(monkeypatch):
    from tailor import latex_resume as latex_mod

    fake = _FakeClient('{"skills": {}, "experience": []}')
    monkeypatch.setattr(latex_mod, "client", fake)
    result = latex_mod.generate_tailored_latex(_job(), {"_archetype": dict(_ARCHETYPE_STUB)})
    assert "latex_source" in result
    _assert_cached_call(fake.sent[0])


def test_form_answers_uses_cached_prefix(monkeypatch):
    fake = _FakeClient(
        '{"why_this_role": "x", "why_this_company": "y", '
        '"additional_info": null, "additional_questions": []}'
    )
    monkeypatch.setattr(fa_mod, "_client", fake)
    fa_mod.generate_form_answers(_job(), {}, archetype_meta=dict(_ARCHETYPE_STUB))
    _assert_cached_call(fake.sent[0])


def test_star_stories_use_cached_prefix(monkeypatch):
    fake = _FakeClient('{"stories": []}')
    monkeypatch.setattr(stories_mod, "_client_lazy", lambda: fake)
    stories_mod.generate_stories(_job(), archetype_meta=dict(_ARCHETYPE_STUB))
    _assert_cached_call(fake.sent[0])


def test_classifier_uses_cached_prefix(monkeypatch):
    fake = _FakeClient(
        '{"archetype": "tier_1a_compneuro", "confidence": 0.8, "reasoning": "x"}'
    )
    monkeypatch.setattr(archetype_mod, "_client_lazy", lambda: fake)
    job = _job()
    job.pop("_archetype")
    archetype_mod.classify_archetype(job)
    _assert_cached_call(fake.sent[0])


def test_prefix_is_big_enough_to_cache():
    """Anthropic only caches prefixes >= 1024 tokens (Sonnet-class).

    Chars/4 is a conservative token estimate; the real prefix is far
    above the floor, so this guards against an accidental gutting of
    the system block (e.g. profile load silently returning empty).
    """
    text = tailor_prompts.cached_system_blocks()[0]["text"]
    assert len(text) / 4 > 1024
