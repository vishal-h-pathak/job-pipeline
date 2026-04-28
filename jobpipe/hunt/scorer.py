from __future__ import annotations

import json
import os
import re

from anthropic import Anthropic

from prompts import build_profile_prompt_string, load_prompt

MODEL = "claude-opus-4-7"

_client = None


def _client_lazy() -> Anthropic:
    global _client
    if _client is None:
        _client = Anthropic(api_key=os.environ["ANTHROPIC_API_KEY"])
    return _client


# System prompt is loaded lazily on first scoring call from
# `prompts/scorer.md` (with `prompts/_shared.md` prepended). Cached after
# first read so each run pays a single file-system hit.
_SYSTEM_CACHE: str | None = None


def _system() -> str:
    global _SYSTEM_CACHE
    if _SYSTEM_CACHE is None:
        _SYSTEM_CACHE = load_prompt("scorer")
    return _SYSTEM_CACHE


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
    resp = _client_lazy().messages.create(
        model=MODEL,
        max_tokens=600,
        system=_system(),
        messages=[{"role": "user", "content": user_msg}],
    )
    text = "".join(block.text for block in resp.content if hasattr(block, "text"))
    result = _extract_json(text)
    # Normalize.
    result["score"] = int(result.get("score", 0))
    tier = result.get("tier")
    if isinstance(tier, str) and tier.isdigit():
        tier = int(tier)
    result["tier"] = tier
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
    return result.get("score", 0) >= 7 and result.get("tier") in (1, 2)
