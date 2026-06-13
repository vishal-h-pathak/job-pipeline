"""tests/test_degree_gate_materials.py — Session I: degree-gate-aware materials.

When the scorer flags ``jobs.degree_gated`` (JD hard-requires MS/PhD,
no equivalent-experience escape hatch), the cover-letter and
form-answers LLM contexts must lead with the BS + nine-years
equivalence case. When the flag is absent or false, no gate framing
may appear — pinned end-to-end through the real prompt assembly with a
fake Anthropic client.
"""

from __future__ import annotations

import pytest

from jobpipe.tailor import pipeline  # noqa: F401 — sys.path bootstrap

import prompts as tailor_prompts
from tailor import cover_letter as cl_mod
from tailor import form_answers as fa_mod

GATE_MARKER = "DEGREE GATE"
EQUIVALENCE_MARKER = "equivalent of the listed degree requirement"

_ARCHETYPE_STUB = {
    "archetype": "tier_1a_compneuro",
    "confidence": 0.9,
    "reasoning": "stub",
}


def _job(degree_gated=None) -> dict:
    job = {
        "id": "test-degree-gate",
        "title": "Research Scientist, Connectomics",
        "company": "TestCo",
        "description": "PhD in neuroscience required.",
        "tier": 1,
        # Pre-stash the archetype so neither call site hits the classifier.
        "_archetype": dict(_ARCHETYPE_STUB),
    }
    if degree_gated is not None:
        job["degree_gated"] = degree_gated
    return job


class _FakeBlock:
    def __init__(self, text: str):
        self.text = text


class _FakeClient:
    """Records every messages.create() kwargs; returns a canned body."""

    def __init__(self, response_text: str):
        self.sent: list[dict] = []
        self._text = response_text
        outer = self

        class _Messages:
            def create(self, **kwargs):
                outer.sent.append(kwargs)

                class _Resp:
                    content = [_FakeBlock(outer._text)]

                return _Resp()

        self.messages = _Messages()

    def all_text(self) -> str:
        chunks = []
        for kwargs in self.sent:
            system = kwargs.get("system")
            if isinstance(system, str):
                chunks.append(system)
            elif isinstance(system, list):
                chunks.extend(
                    b.get("text", "") if isinstance(b, dict) else str(b)
                    for b in system
                )
            for msg in kwargs.get("messages", []):
                content = msg.get("content")
                if isinstance(content, str):
                    chunks.append(content)
                elif isinstance(content, list):
                    chunks.extend(
                        b.get("text", "") if isinstance(b, dict) else str(b)
                        for b in content
                    )
        return "\n".join(chunks)


def test_degree_gate_block_helper():
    assert GATE_MARKER in tailor_prompts.degree_gate_block({"degree_gated": True})
    assert tailor_prompts.degree_gate_block({"degree_gated": False}) == ""
    assert tailor_prompts.degree_gate_block({}) == ""


@pytest.mark.parametrize("gated", [True, False])
def test_cover_letter_context_gates_correctly(monkeypatch, gated):
    fake = _FakeClient("A cover letter body.")
    monkeypatch.setattr(cl_mod, "client", fake)

    cl_mod.generate_cover_letter(_job(degree_gated=gated))

    text = fake.all_text()
    assert (GATE_MARKER in text) is gated
    assert (EQUIVALENCE_MARKER in text) is gated


@pytest.mark.parametrize("gated", [True, False])
def test_form_answers_context_gates_correctly(monkeypatch, gated):
    fake = _FakeClient(
        '{"why_this_role": "x", "why_this_company": "y", '
        '"additional_info": null, "additional_questions": []}'
    )
    monkeypatch.setattr(fa_mod, "_client", fake)

    fa_mod.generate_form_answers(
        _job(degree_gated=gated),
        resume_result={},
        archetype_meta=dict(_ARCHETYPE_STUB),
    )

    text = fake.all_text()
    assert (GATE_MARKER in text) is gated
    assert (EQUIVALENCE_MARKER in text) is gated


def test_missing_flag_means_no_gate_framing(monkeypatch):
    """Rows written before migration 010 carry no degree_gated key at all."""
    fake = _FakeClient("A cover letter body.")
    monkeypatch.setattr(cl_mod, "client", fake)
    cl_mod.generate_cover_letter(_job())
    assert GATE_MARKER not in fake.all_text()
