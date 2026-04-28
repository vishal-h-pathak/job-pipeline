"""
tailor/cover_letter.py — Claude-powered cover letter generation.

Generates a personalized cover letter for each job application.
"""

import logging
from pathlib import Path
from datetime import datetime

import anthropic
from config import ANTHROPIC_API_KEY, CLAUDE_MODEL, CANDIDATE_PROFILE_PATH
from prompts import load_profile, load_prompt
from tailor.archetype import classify_archetype, render_archetype_block

logger = logging.getLogger("tailor.cover_letter")

client = anthropic.Anthropic(api_key=ANTHROPIC_API_KEY)


def load_candidate_profile() -> str:
    """Load the merged user-layer candidate profile.

    Reads from `profile/` (canonically in the sibling `job-hunter` repo)
    and falls back to legacy `CLAUDE.md` if `profile/` is missing.
    """
    return load_profile()


def generate_cover_letter(job: dict, resume_tailoring: dict = None) -> dict:
    """
    Generate a tailored cover letter for a specific job posting.

    Args:
        job: Dict with job details (title, company, description, etc.)
        resume_tailoring: Optional output from tailor_resume() to maintain consistency.

    Returns:
        Dict with:
            - cover_letter: str — the full cover letter text
            - output_path: str — path to the saved file
    """
    profile = load_candidate_profile()
    job_desc = job.get("description", "")
    job_title = job.get("title", "Unknown")
    company = job.get("company", "Unknown")

    context = ""
    if resume_tailoring:
        context = f"""
RESUME TAILORING CONTEXT (maintain consistency with these choices):
- Summary: {resume_tailoring.get('tailored_summary', '')}
- Emphasis areas: {', '.join(resume_tailoring.get('emphasis_areas', []))}
- Keywords: {', '.join(resume_tailoring.get('keywords_to_include', []))}
"""

    # Load voice profile if available
    voice_profile = ""
    voice_path = Path(__file__).parent.parent / "templates" / "VOICE_PROFILE.md"
    if voice_path.exists():
        voice_profile = voice_path.read_text(encoding="utf-8")

    # Optional Match Agent transcript — direct quotes from Vishal's own
    # conversation about this role, captured in the dashboard chat. When
    # present, the cover letter should ground its angle, anecdotes, and
    # framing in what he actually said rather than in generic inferences.
    match_chat = (job.get("match_chat_transcript") or "").strip()
    match_chat_block = (
        f"\n\nMATCH AGENT INTERVIEW (Vishal's own answers about THIS role — "
        f"use his framing, motivations, and emphasis areas verbatim where they "
        f"fit. If a draft cover letter or bullet suggestions appear at the end "
        f"of the transcript, treat them as a starting reference, not as final "
        f"output — rewrite anything that doesn't match his voice profile):\n"
        f"{match_chat}\n"
        if match_chat else ""
    )

    # Archetype (J-4). Reuse the resume-tailoring run's classification
    # if present (`resume_tailoring['_archetype']`); otherwise reuse the
    # job's stash; otherwise classify here.
    archetype_meta = (
        (resume_tailoring or {}).get("_archetype")
        or job.get("_archetype")
        or classify_archetype(job)
    )
    job["_archetype"] = archetype_meta
    archetype_block = render_archetype_block(archetype_meta.get("archetype", ""))

    prompt = load_prompt(
        "tailor_cover_letter",
        voice_profile=voice_profile,
        profile=profile,
        job_title=job_title,
        company=company,
        job_desc=job_desc,
        tier=job.get("tier", "unknown"),
        context=context,
        match_chat_block=match_chat_block,
        archetype_block=archetype_block,
    )

    response = client.messages.create(
        model=CLAUDE_MODEL,
        max_tokens=1500,
        messages=[{"role": "user", "content": prompt}],
    )

    cover_letter = response.content[0].text.strip()

    logger.info(f"Cover letter generated for {company} — {job_title}")
    return {
        "cover_letter": cover_letter,
    }
