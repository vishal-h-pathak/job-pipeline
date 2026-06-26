from __future__ import annotations

import json
import os
import re

# Canonical package path, not the hunt-local bare `prompts` import the
# other hunt modules use: bare `prompts` collides in sys.modules with
# the tailor subtree's own `prompts` package when both subtrees load in
# one process (pytest collects both; the rescore path imports scorer
# alongside tailor modules). The hunt prompts package is cross-cutting
# anyway — it already imports jobpipe.profile_loader.
from jobpipe.hunt.prompts import build_profile_prompt_string, load_prompt

# Credits-first → Max-plan OAuth fallback (feat/hunt-resolver-aggregator).
# Routing the scorer through the shared helper gives the hunt the same
# auth resilience the tailor got: on a depleted/benched ANTHROPIC_API_KEY
# it falls through to subscription OAuth instead of failing the run.
from jobpipe.shared import llm

# Cost control (2026-06): the scorer makes one call per surviving posting,
# so model choice dominates daily hunt spend. Dropped Opus 4.7 → Sonnet 4.6
# ($5/$25 → $3/$15 per Mtok in/out) to cut per-run cost; this also realigns
# the code with hunt/README.md, which already documents Sonnet. Override via
# the HUNT_SCORER_MODEL env knob if a future run wants a different tier.
MODEL = os.getenv("HUNT_SCORER_MODEL") or "claude-sonnet-4-6"


# System prompt is loaded lazily on first scoring call from
# `prompts/scorer.md` (with `prompts/_shared.md` prepended). Cached after
# first read so each run pays a single file-system hit.
_SYSTEM_CACHE: str | None = None


def _system() -> str:
    global _SYSTEM_CACHE
    if _SYSTEM_CACHE is None:
        _SYSTEM_CACHE = load_prompt("scorer")
    return _SYSTEM_CACHE


def _normalize_tier(tier):
    """Canonicalize the model's tier output.

    Whole-number tiers become ints (1, 2, 3 — matching pre-thesis
    behavior), the half tier becomes float 1.5 whether the model emitted
    "1.5" or 1.5, and non-numeric values ("disqualify") pass through
    unchanged. The jobs.tier column is text, so all of these serialize
    losslessly.
    """
    if isinstance(tier, str):
        try:
            tier = float(tier)
        except ValueError:
            return tier
    if isinstance(tier, float) and tier.is_integer():
        return int(tier)
    return tier


def _extract_json(text: str) -> dict:
    text = text.strip()
    # Strip code fences if the model added any.
    text = re.sub(r"^```(?:json)?|```$", "", text, flags=re.MULTILINE).strip()
    start = text.find("{")
    end = text.rfind("}")
    if start == -1 or end == -1:
        raise ValueError(f"no JSON object in response: {text!r}")
    return json.loads(text[start : end + 1])


def score_job(title: str, company: str, description: str, location: str) -> dict:
    profile = build_profile_prompt_string()
    user_msg = (
        "=== PROFILE ===\n"
        f"{profile}\n\n"
        "=== JOB POSTING ===\n"
        f"Title: {title}\n"
        f"Company: {company}\n"
        f"Location: {location}\n"
        f"Description:\n{description}\n"
    )
    text = llm.complete(
        system=_system(),
        prompt=user_msg,
        model=MODEL,
        max_tokens=600,
    )
    result = _extract_json(text)
    # Normalize.
    result["score"] = int(result.get("score", 0))
    result["tier"] = _normalize_tier(result.get("tier"))
    # Degree gate (thesis.md): MS/PhD hard-required with no
    # equivalent-experience escape hatch. Defaults False so older
    # prompt outputs and partial JSON stay valid.
    result["degree_gated"] = bool(result.get("degree_gated", False))
    # Posting legitimacy axis (J-2). Defaults to proceed_with_caution if
    # the model omitted it — never None — so downstream code can always
    # rely on a known categorical value.
    legitimacy = (result.get("legitimacy") or "").strip().lower()
    if legitimacy not in {"high_confidence", "proceed_with_caution", "suspicious"}:
        legitimacy = "proceed_with_caution"
    result["legitimacy"] = legitimacy
    result["legitimacy_reasoning"] = (result.get("legitimacy_reasoning") or "").strip()
    return result


def should_notify(result: dict) -> bool:
    """Decide whether a scored job should fire a notification.

    Legitimacy is intentionally NOT a hard gate. A "suspicious" posting
    that scores well on fit still notifies — Vishal can decide whether
    the risk is worth it. Suspicious legitimacy surfaces as a colored
    pill in the dashboard review panel; that's where the soft-warning
    signal lives.
    """
    if result.get("recommended_action") == "notify":
        return True
    # Defensive int() coercion mirrors the pipeline.py site — guards
    # against the same score-as-string drift that crashed the tailor's
    # form_answers gate. See the TODO in
    # jobpipe/tailor/pipeline.py::process_approved_jobs.
    try:
        score_val = int(result.get("score", 0) or 0)
    except (TypeError, ValueError):
        score_val = 0
    tier_val = result.get("tier")
    try:
        tier_val = float(tier_val) if tier_val is not None else None
    except (TypeError, ValueError):
        pass
    # Tier 1.5 (thesis.md: agentic / applied AI engineering) notifies on
    # the same score bar as Tiers 1 and 2 — it ranks above Tier 2.
    return score_val >= 7 and tier_val in (1, 1.5, 2)
