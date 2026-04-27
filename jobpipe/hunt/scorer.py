import json
import os
import pathlib
import re

from anthropic import Anthropic

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


SYSTEM = """You are a job-fit evaluator for Vishal Pathak. The user message
contains his full profile (his "ground truth" doc) followed by a single job
posting. Score how well the job matches his interests, tier, location, and
disqualifiers.

Respond with ONLY a JSON object (no prose, no code fences) of the form:
{
  "score": <int 1-10>,
  "tier": <1 | 2 | 3 | "disqualify">,
  "reasoning": "<2-3 sentences>",
  "recommended_action": "notify" | "skip" | "disqualify"
}

Rules:
- Tier 1 (computational neuroscience, neuromorphic, connectomics, embodied
  sim, BCI) → almost always "notify" if score >= 7.
- Tier 2 (sales engineering in genuinely interesting AI/LLM domains) →
  "notify" if score >= 7.
- Tier 3 (mission-driven ML/CV) → "notify" only if score >= 8.
- Anything matching disqualifiers (DoD, defense, government, no clear
  mission) → tier "disqualify", action "disqualify".
- Otherwise "skip".
"""


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
        system=SYSTEM,
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
