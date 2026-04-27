from __future__ import annotations

import json
import os
import pathlib
import re

from anthropic import Anthropic

from prompts import load_prompt

MODEL = "claude-opus-4-7"
PROFILE_PATH = pathlib.Path(__file__).parent / "CLAUDE.md"

_client = None


def _client_lazy() -> Anthropic:
    global _client
    if _client is None:
        _client = Anthropic(api_key=os.environ["ANTHROPIC_API_KEY"])
    return _client


def _profile() -> str:
    return PROFILE_PATH.read_text()


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
    profile = _profile()
    user_msg = (
        "=== PROFILE (CLAUDE.md) ===\n"
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
    return result


def should_notify(result: dict) -> bool:
    if result.get("recommended_action") == "notify":
        return True
    return result.get("score", 0) >= 7 and result.get("tier") in (1, 2)
