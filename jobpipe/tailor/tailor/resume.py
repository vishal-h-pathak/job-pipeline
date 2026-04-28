"""
tailor/resume.py — Claude-powered resume tailoring.

Takes the base resume data + job description, generates a tailored version
that emphasizes relevant experience and skills.
"""

import json
import logging
from pathlib import Path
from datetime import datetime

import anthropic
from jobpipe.config import ANTHROPIC_API_KEY, TAILOR_CLAUDE_MODEL as CLAUDE_MODEL
from jobpipe.tailor.paths import CANDIDATE_PROFILE_PATH
from prompts import load_profile, load_prompt
from tailor.archetype import classify_archetype, render_archetype_block

# J-9: emit a one-time warning to the log if cv.md / article-digest.md /
# BASE_RESUME / CLAUDE.md disagree on any anchored numeric claim. Never
# blocks tailoring — just surfaces drift before any LLM call goes out.
try:
    from scripts.cv_sync_check import warn_if_drift as _cv_warn_if_drift
    _cv_warn_if_drift()
except Exception:
    # Drift check is best-effort. Tailoring proceeds even if the script
    # fails to import or run.
    pass

logger = logging.getLogger("tailor.resume")

client = anthropic.Anthropic(api_key=ANTHROPIC_API_KEY)


def load_candidate_profile() -> str:
    """Load the merged user-layer candidate profile.

    Reads from `profile/` (top-level) + `jobpipe/hunt/profile/`
    and falls back to legacy `CLAUDE.md` if `profile/` is missing.
    """
    return load_profile()


def tailor_resume(job: dict) -> dict:
    """
    Generate a tailored resume for a specific job posting.

    Args:
        job: Dict with keys: title, company, description, location, url, score, tier, reasoning

    Returns:
        Dict with:
            - tailored_summary: str — the tailored professional summary
            - emphasis_areas: list[str] — which skills/experience to highlight
            - output_path: str — path to the generated resume file
            - diff_notes: str — what changed from the base resume
    """
    profile = load_candidate_profile()
    job_desc = job.get("description", "")
    job_title = job.get("title", "Unknown")
    company = job.get("company", "Unknown")

    # Load voice profile if available
    voice_profile = ""
    voice_path = Path(__file__).parent.parent / "templates" / "VOICE_PROFILE.md"
    if voice_path.exists():
        voice_profile = voice_path.read_text(encoding="utf-8")

    # Optional Match Agent transcript — captured from the dashboard chat. When
    # present, it carries Vishal's own framing of why the role matters and
    # which experiences to lean into. Treat it as authoritative for this
    # specific application; CLAUDE.md remains the ground truth for facts.
    match_chat = (job.get("match_chat_transcript") or "").strip()
    match_chat_block = (
        f"\n\nMATCH AGENT INTERVIEW (Vishal's own framing for THIS specific role — "
        f"prioritize this over generic cover-letter logic when shaping emphasis areas, "
        f"keywords, and experience order):\n{match_chat}\n"
        if match_chat else ""
    )

    # Archetype routing (J-4). If the caller already classified once and
    # stashed `_archetype` on the job dict, reuse it — saves a duplicate
    # classifier call when the orchestrator runs resume + cover letter
    # back-to-back. Otherwise classify here.
    archetype_meta = job.get("_archetype")
    if not archetype_meta:
        archetype_meta = classify_archetype(job)
        job["_archetype"] = archetype_meta
    archetype_key = archetype_meta.get("archetype", "")
    archetype_block = render_archetype_block(archetype_key)
    if archetype_key:
        logger.info(
            "Archetype: %s (confidence=%.2f) — %s",
            archetype_key,
            archetype_meta.get("confidence", 0.0),
            archetype_meta.get("reasoning", ""),
        )

    prompt = load_prompt(
        "tailor_resume",
        voice_profile=voice_profile,
        profile=profile,
        job_title=job_title,
        company=company,
        job_desc=job_desc,
        tier=job.get("tier", "unknown"),
        match_chat_block=match_chat_block,
        archetype_block=archetype_block,
    )

    response = client.messages.create(
        model=CLAUDE_MODEL,
        max_tokens=2000,
        messages=[{"role": "user", "content": prompt}],
    )

    response_text = response.content[0].text

    # Parse JSON from response (handle markdown code blocks)
    if "```json" in response_text:
        response_text = response_text.split("```json")[1].split("```")[0]
    elif "```" in response_text:
        response_text = response_text.split("```")[1].split("```")[0]

    result = json.loads(response_text.strip())

    # Stamp archetype into the result so downstream callers (latex_resume,
    # cover_letter, the orchestrator's status writer) can persist it
    # without re-running the classifier.
    result["_archetype"] = archetype_meta

    logger.info(f"Resume tailored for {company} — {job_title}")
    return result
