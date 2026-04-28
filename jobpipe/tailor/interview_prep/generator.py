"""interview_prep/generator.py — Generate STAR+R stories per job (J-3).

Single Sonnet call per job. Reads profile + archetype + JD, returns 3-5
stories grounded in Vishal's real experience. The +R (Reflection) is
the part that makes a story memorable — what he learned or would do
differently — and it's enforced in the prompt.
"""

from __future__ import annotations

import json
import logging
import re
from typing import Optional

import anthropic

from jobpipe.config import ANTHROPIC_API_KEY, TAILOR_CLAUDE_MODEL as CLAUDE_MODEL
from prompts import load_profile, load_prompt
from tailor.archetype import classify_archetype, render_archetype_block

logger = logging.getLogger("interview_prep.generator")

_client: Optional[anthropic.Anthropic] = None


def _client_lazy() -> anthropic.Anthropic:
    global _client
    if _client is None:
        _client = anthropic.Anthropic(api_key=ANTHROPIC_API_KEY)
    return _client


def _extract_json(text: str) -> dict:
    text = text.strip()
    text = re.sub(r"^```(?:json)?|```$", "", text, flags=re.MULTILINE).strip()
    start = text.find("{")
    end = text.rfind("}")
    if start == -1 or end == -1:
        raise ValueError(f"no JSON object in response: {text!r}")
    return json.loads(text[start:end + 1])


def generate_stories(job: dict, archetype_meta: Optional[dict] = None) -> list[dict]:
    """Generate STAR+R stories for one job.

    Args:
        job: Full job dict (title, company, description, ...).
        archetype_meta: Optional pre-classified archetype to avoid an
            extra Sonnet call. If None, classifies here.

    Returns:
        List of story dicts with keys: situation, task, action, result,
        reflection, tags. Empty list on failure.
    """
    archetype_meta = archetype_meta or classify_archetype(job)
    archetype_key = archetype_meta.get("archetype", "")

    prompt = load_prompt(
        "star_stories",
        profile=load_profile(),
        archetype_block=render_archetype_block(archetype_key) or "(no archetype)",
        job_title=job.get("title", ""),
        company=job.get("company", ""),
        job_desc=(job.get("description", "") or "")[:5000],
    )

    try:
        resp = _client_lazy().messages.create(
            model=CLAUDE_MODEL,
            max_tokens=3000,
            messages=[{"role": "user", "content": prompt}],
        )
        text = "".join(b.text for b in resp.content if hasattr(b, "text"))
        result = _extract_json(text)
    except Exception as exc:
        logger.warning("STAR+R generation failed for %s: %s", job.get("id"), exc)
        return []

    stories = result.get("stories") or []
    # Defensive normalization — drop malformed rows so we never write
    # a story without all four-plus-one fields populated.
    cleaned: list[dict] = []
    for s in stories:
        situation = (s.get("situation") or "").strip()
        task = (s.get("task") or "").strip()
        action = (s.get("action") or "").strip()
        story_result = (s.get("result") or "").strip()
        reflection = (s.get("reflection") or "").strip()
        if not (situation and task and action and story_result and reflection):
            continue
        tags = list(s.get("tags") or [])
        # Always tag with the archetype so /dashboard/stories filtering
        # by lane works even if the LLM forgot.
        if archetype_key and archetype_key not in tags:
            tags.append(archetype_key)
        cleaned.append(
            {
                "situation": situation,
                "task": task,
                "action": action,
                "result": story_result,
                "reflection": reflection,
                "tags": tags,
            }
        )
    return cleaned
