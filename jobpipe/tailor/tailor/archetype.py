"""tailor/archetype.py — Archetype classifier + config loader (J-4).

Loads archetype definitions from ``profile.yml::archetypes`` via the
single canonical loader at ``jobpipe.profile_loader.load_archetypes``.
Classifies a JD into the best-fit archetype with a single Sonnet-class
call, then exposes the archetype config for downstream prompts
(``tailor_resume.md``, ``tailor_cover_letter.md``).

The classifier is intentionally cheap. It reads only title +
description + the framings YAML — no profile injection, no resume.
Output is a single archetype key + confidence; downstream tailoring
prompts get the full framing/emphasis/tone/bullet_template via
``render_archetype_block(key)``.

PR-4 carryover-from-PR-2: replaced the bespoke
``_resolve_profile_yml`` / ``_load_archetypes`` pair (which walked up
to ``../job-hunter/profile/`` from the pre-merge ``job-applicant``
layout) with a delegation to ``jobpipe.profile_loader.load_archetypes``.
The ``@lru_cache`` on ``profile_loader.profile_dir`` already provides
the memoization the old ``_ARCHETYPES_CACHE`` global used to provide.
"""

from __future__ import annotations

import json
import logging
import re

import anthropic

from config import ANTHROPIC_API_KEY, CLAUDE_MODEL
from prompts import load_prompt
from jobpipe.profile_loader import load_archetypes

logger = logging.getLogger("tailor.archetype")

_FALLBACK_KEY = "tier_3_mission_ml"


def _load_archetypes() -> dict:
    """Return the parsed ``archetypes:`` block from profile.yml.

    Thin wrapper kept for the call sites below; profile_loader handles
    resolution + caching + the empty-dict fallback when profile.yml is
    missing.
    """
    archs = load_archetypes()
    if not archs:
        logger.warning("profile.yml archetypes block empty — archetype routing disabled")
    return archs


def archetype_keys() -> list[str]:
    """Return the list of valid archetype keys (for tests + classifier)."""
    return list(_load_archetypes().keys())


def archetype_config(key: str) -> tuple[str, dict]:
    """Look up one archetype's config; falls back to `tier_3_mission_ml`.

    Returns (resolved_key, cfg). The resolved key is `key` if it exists,
    or `_FALLBACK_KEY` if a fallback was applied, or "" if nothing
    matched at all.
    """
    archs = _load_archetypes()
    if key in archs:
        return key, archs[key]
    if _FALLBACK_KEY in archs:
        return _FALLBACK_KEY, archs[_FALLBACK_KEY]
    return "", {}


def render_archetype_block(key: str) -> str:
    """Render an archetype's framing/emphasis/tone/bullet template into a
    single string block that downstream prompts inject as
    `{archetype_block}`. Empty string if the key resolves to nothing —
    callers should be ok with that (tailoring still works without
    archetype routing).
    """
    resolved_key, cfg = archetype_config(key)
    if not cfg:
        return ""
    parts = [f"ARCHETYPE: {resolved_key}"]
    if cfg.get("label"):
        parts.append(f"LABEL: {cfg['label']}")
    if cfg.get("framing"):
        parts.append(f"FRAMING:\n{cfg['framing'].strip()}")
    proof = cfg.get("emphasis_proof_points") or []
    if proof:
        bullets = "\n".join(f"- {p}" for p in proof)
        parts.append(f"EMPHASIS PROOF POINTS:\n{bullets}")
    if cfg.get("tone_guidance"):
        parts.append(f"TONE GUIDANCE:\n{cfg['tone_guidance'].strip()}")
    if cfg.get("bullet_template"):
        parts.append(f"BULLET TEMPLATE:\n{cfg['bullet_template'].strip()}")
    return "\n\n".join(parts)


def _archetypes_block_for_classifier() -> str:
    """Render the archetype labels + framings for the classifier prompt."""
    archs = _load_archetypes()
    parts = []
    for key, cfg in archs.items():
        parts.append(
            f"--- {key} ---\n"
            f"label: {cfg.get('label', '')}\n"
            f"framing: {(cfg.get('framing') or '').strip()}"
        )
    return "\n\n".join(parts)


def _extract_json(text: str) -> dict:
    text = text.strip()
    text = re.sub(r"^```(?:json)?|```$", "", text, flags=re.MULTILINE).strip()
    start = text.find("{")
    end = text.rfind("}")
    if start == -1 or end == -1:
        raise ValueError(f"no JSON object in response: {text!r}")
    return json.loads(text[start:end + 1])


_client = None


def _client_lazy() -> anthropic.Anthropic:
    global _client
    if _client is None:
        _client = anthropic.Anthropic(api_key=ANTHROPIC_API_KEY)
    return _client


def classify_archetype(job: dict) -> dict:
    """Classify a JD into one archetype key with a cheap Sonnet call.

    Returns a dict {archetype, confidence, reasoning}. Falls back to
    `tier_3_mission_ml` if the response can't be parsed or no
    archetypes are configured.
    """
    archs = _load_archetypes()
    if not archs:
        return {
            "archetype": _FALLBACK_KEY,
            "confidence": 0.0,
            "reasoning": "no archetypes configured",
        }

    prompt = load_prompt(
        "classify_archetype",
        archetypes_block=_archetypes_block_for_classifier(),
        job_title=job.get("title", ""),
        company=job.get("company", ""),
        job_desc=(job.get("description", "") or "")[:4000],
    )

    try:
        resp = _client_lazy().messages.create(
            model=CLAUDE_MODEL,
            max_tokens=300,
            messages=[{"role": "user", "content": prompt}],
        )
        text = "".join(b.text for b in resp.content if hasattr(b, "text"))
        result = _extract_json(text)
    except Exception as exc:
        logger.warning("archetype classify failed: %s — falling back", exc)
        return {
            "archetype": _FALLBACK_KEY,
            "confidence": 0.0,
            "reasoning": f"classifier error: {exc}",
        }

    key = (result.get("archetype") or "").strip()
    if key not in archs:
        logger.info("classifier returned unknown archetype %r — using fallback", key)
        key = _FALLBACK_KEY
        result["reasoning"] = (result.get("reasoning") or "") + " (fallback applied)"

    result["archetype"] = key
    try:
        result["confidence"] = float(result.get("confidence") or 0.0)
    except (TypeError, ValueError):
        result["confidence"] = 0.0
    return result
