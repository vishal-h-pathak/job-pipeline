"""tailor/form_answers.py — Generate structured form-answer drafts (M-1).

The career-ops "Block H" pattern: produce a JSON of standard application-
form fields. Identity / contact / location / compensation / work-
authorization / current-employment fields are pulled from `profile.yml`
in Python (the LLM never regenerates these to avoid hallucinated phone
numbers, emails, or salary figures). The LLM only drafts the four
narrative fields:

  - why_this_role
  - why_this_company
  - additional_info
  - additional_questions  (role-specific questions the JD explicitly asks)

The output of `generate_form_answers()` is persisted to the
`jobs.form_answers` JSONB column (added in migration 007) and becomes
the authoritative source for both the per-ATS DOM handlers (M-3) and
the dashboard cockpit (M-6).
"""

from __future__ import annotations

import json
import logging
import re
from typing import Any, Optional

import anthropic

from jobpipe.config import ANTHROPIC_API_KEY, TAILOR_CLAUDE_MODEL as CLAUDE_MODEL
from jobpipe import profile_loader
from prompts import load_profile, load_prompt
from tailor.archetype import render_archetype_block

logger = logging.getLogger("tailor.form_answers")

_client = anthropic.Anthropic(api_key=ANTHROPIC_API_KEY) if ANTHROPIC_API_KEY else None


# ── Profile loading ────────────────────────────────────────────────────────

def _load_profile_yaml() -> dict:
    """Return the parsed `profile/profile.yml` via `jobpipe.profile_loader`.

    The loader resolves the user-layer profile dir once (walks up to the
    repo's `pyproject.toml` or honors `JOBPIPE_PROFILE_DIR`) and returns
    `{}` when the file is missing — same fallback shape as the previous
    local implementation, so downstream `.get(...) or {}` calls keep
    working.
    """
    return profile_loader.load_profile()


# ── Identity block (pure Python — never LLM-generated) ─────────────────────

def _split_full_name(full_name: str) -> tuple[str, str]:
    parts = (full_name or "").strip().split()
    if not parts:
        return ("", "")
    if len(parts) == 1:
        return (parts[0], "")
    return (parts[0], " ".join(parts[1:]))


def _ensure_url(value: str) -> str:
    if not value:
        return ""
    v = str(value).strip()
    if v.startswith(("http://", "https://")):
        return v
    return f"https://{v}"


def _format_salary_range(comp_str: str) -> str:
    """Convert '120000-140000' / '120k-140k' / 120000 to '$120,000 - $140,000'."""
    if comp_str is None or comp_str == "":
        return ""
    nums = re.findall(r"\d+", str(comp_str).replace(",", ""))
    if len(nums) >= 2:
        lo, hi = int(nums[0]), int(nums[1])
        if lo < 1000:
            lo *= 1000
        if hi < 1000:
            hi *= 1000
        return f"${lo:,} - ${hi:,}"
    if len(nums) == 1:
        n = int(nums[0])
        if n < 1000:
            n *= 1000
        return f"${n:,}"
    return str(comp_str)


def _build_identity_block(profile_yaml: dict) -> dict[str, Any]:
    """Assemble identity / contact / location / comp / work-auth / current
    employment from profile.yml. The LLM never sees this as a target to
    regenerate — these values are merged into the final output AFTER the
    model returns."""
    identity = profile_yaml.get("identity") or {}
    loc_comp = profile_yaml.get("location_and_compensation") or {}
    form_defaults = profile_yaml.get("application_defaults") or {}

    full_name = identity.get("name") or "Vishal Pathak"
    first, last = _split_full_name(full_name)

    salary_target = _format_salary_range(loc_comp.get("target_comp_usd", ""))

    work_auth_raw = (form_defaults.get("work_authorization") or "").strip()
    sponsorship = form_defaults.get("visa_sponsorship_needed")
    if work_auth_raw == "us_citizen":
        work_auth_phrase = "US citizen, no sponsorship needed"
    elif work_auth_raw:
        sp = (
            "no sponsorship needed"
            if sponsorship is False
            else "sponsorship may be needed"
        )
        work_auth_phrase = f"{work_auth_raw}, {sp}"
    else:
        work_auth_phrase = ""

    relocation_str = (
        loc_comp.get("relocation")
        or form_defaults.get("relocation_willingness")
        or ""
    )
    remote_pref = (
        loc_comp.get("in_person_acceptable")
        or form_defaults.get("in_person_willingness")
        or ""
    )

    return {
        # Identity
        "first_name": first,
        "last_name": last,
        "full_name": full_name,
        "email": identity.get("email") or "",
        "phone": identity.get("phone"),
        "linkedin_url": _ensure_url(identity.get("linkedin", "")),
        "github_url": _ensure_url(identity.get("github", "")) or None,
        "portfolio_url": _ensure_url(identity.get("website", "")),
        # Location & comp
        "current_location": (
            identity.get("location_base")
            or loc_comp.get("base")
            or "Atlanta, GA"
        ),
        "willing_to_relocate": relocation_str,
        "remote_preference": remote_pref,
        "salary_expectation": salary_target,
        "work_authorization": work_auth_phrase,
        "notice_period": "2 weeks",
        "availability_to_start": (
            form_defaults.get("earliest_start_date")
            or "Standard 2-week notice"
        ),
        # Current employment (profile.yml doesn't carry these today;
        # CLAUDE.md narrative establishes GTRI as current)
        "current_company": (
            identity.get("current_company")
            or "Georgia Tech Research Institute"
        ),
        "current_title": (
            identity.get("current_title")
            or "Algorithms & Analysis Engineer"
        ),
        "years_of_experience": identity.get("years_of_experience") or 7,
        # Effectively-required Anthropic-style form fields. The submitter's
        # adapters/_common.applicant_fields reads these from form_answers
        # so the three-tier classifier can answer them instead of
        # routing to review. Visa-needed and prior-interview ship as the
        # raw structures from profile.yml; the submit-side helpers
        # (_prior_interview_summary etc.) cope with both shapes.
        "visa_sponsorship_needed": form_defaults.get("visa_sponsorship_needed"),
        "ai_policy_ack": (form_defaults.get("ai_policy_ack") or "").strip(),
        "previous_interview_with_company": form_defaults.get(
            "previous_interview_with_company"
        ) or {},
    }


# ── Prompt context helpers ─────────────────────────────────────────────────

def _identity_summary_for_prompt(identity: dict[str, Any]) -> str:
    lines = []
    for k, v in identity.items():
        if v is None or v == "":
            continue
        lines.append(f"  {k}: {v}")
    return "\n".join(lines)


def _resume_context_for_prompt(resume_result: dict | None) -> str:
    if not resume_result:
        return "(no resume tailoring context available)"
    parts = []
    if resume_result.get("tailored_summary"):
        parts.append(f"Summary: {resume_result['tailored_summary']}")
    if resume_result.get("emphasis_areas"):
        parts.append(
            f"Emphasis areas: {', '.join(resume_result['emphasis_areas'])}"
        )
    if resume_result.get("keywords_to_include"):
        parts.append(
            f"Keywords: {', '.join(resume_result['keywords_to_include'])}"
        )
    return "\n".join(parts) if parts else "(no resume tailoring context)"


def _voice_profile() -> str:
    """Return the full voice-profile markdown for prompt injection."""
    return profile_loader.load_voice_profile().get("raw", "") or ""


def _extract_json_object(text: str) -> dict:
    """Pull the first balanced JSON object out of a model response."""
    fenced = re.search(r"```(?:json)?\s*(\{.*?\})\s*```", text, flags=re.DOTALL)
    if fenced:
        return json.loads(fenced.group(1))
    start = text.find("{")
    end = text.rfind("}")
    if start >= 0 and end > start:
        return json.loads(text[start : end + 1])
    raise ValueError(
        f"No JSON object found in model response: {text[:300]!r}"
    )


# ── Public entry point ─────────────────────────────────────────────────────

def generate_form_answers(
    job: dict,
    resume_result: dict,
    archetype_meta: Optional[dict] = None,
) -> dict[str, Any]:
    """Generate structured form-answer drafts for a job application.

    Identity / contact / location / comp / work-auth / current-employment
    fields are filled from `profile.yml` in Python and never touch the
    model. The LLM produces only `why_this_role`, `why_this_company`,
    `additional_info`, and `additional_questions`.

    Returns a dict matching the M-1 schema. Raises on profile-load
    failure or unrecoverable model-output failure so the caller can
    decide whether to fail the whole tailoring run or continue.
    """
    if _client is None:
        raise RuntimeError(
            "ANTHROPIC_API_KEY not set; cannot generate form_answers"
        )

    profile_yaml = _load_profile_yaml()
    identity = _build_identity_block(profile_yaml)

    archetype_key = (archetype_meta or {}).get("archetype", "")
    archetype_block = (
        render_archetype_block(archetype_key)
        if archetype_key
        else "(no archetype classified)"
    )

    prompt = load_prompt(
        "form_answers",
        identity_summary=_identity_summary_for_prompt(identity),
        voice_profile=_voice_profile(),
        profile=load_profile(),
        archetype_block=archetype_block,
        resume_context=_resume_context_for_prompt(resume_result),
        job_title=job.get("title", ""),
        company=job.get("company", ""),
        job_desc=(job.get("description", "") or "")[:6000],
        tier=job.get("tier", "unknown"),
    )

    response = _client.messages.create(
        model=CLAUDE_MODEL,
        max_tokens=2000,
        messages=[{"role": "user", "content": prompt}],
    )

    raw = response.content[0].text.strip()
    try:
        narrative = _extract_json_object(raw)
    except (ValueError, json.JSONDecodeError) as exc:
        logger.error(
            f"form_answers JSON parse failed: {exc}\nRaw: {raw[:500]}"
        )
        raise

    additional_questions = narrative.get("additional_questions") or []
    if not isinstance(additional_questions, list):
        additional_questions = []
    cleaned_qs = []
    for q in additional_questions:
        if isinstance(q, dict) and q.get("question") and q.get("draft_answer"):
            cleaned_qs.append({
                "question": str(q["question"]).strip(),
                "draft_answer": str(q["draft_answer"]).strip(),
            })

    additional_info = narrative.get("additional_info")
    if isinstance(additional_info, str):
        additional_info = additional_info.strip() or None

    return {
        **identity,
        "why_this_role": (narrative.get("why_this_role") or "").strip(),
        "why_this_company": (narrative.get("why_this_company") or "").strip(),
        "additional_info": additional_info,
        "additional_questions": cleaned_qs,
    }
