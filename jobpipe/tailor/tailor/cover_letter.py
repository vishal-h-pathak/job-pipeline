"""
tailor/cover_letter.py — Claude-powered cover letter generation.

Generates a personalized cover letter for each job application.
"""

import logging
from pathlib import Path
from datetime import datetime

import anthropic
from config import ANTHROPIC_API_KEY, CLAUDE_MODEL, CANDIDATE_PROFILE_PATH

logger = logging.getLogger("tailor.cover_letter")

client = anthropic.Anthropic(api_key=ANTHROPIC_API_KEY)


def load_candidate_profile() -> str:
    """Load the CLAUDE.md candidate profile."""
    if CANDIDATE_PROFILE_PATH.exists():
        return CANDIDATE_PROFILE_PATH.read_text(encoding="utf-8")
    raise FileNotFoundError(f"Candidate profile not found at {CANDIDATE_PROFILE_PATH}")


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

    prompt = f"""You are writing a cover letter for Vishal Pathak. This is the most important
instruction: the letter must sound like Vishal wrote it himself — not like an AI, not like
a career coach, not like a template. Read the voice profile carefully and match his tone exactly.

VOICE PROFILE:
{voice_profile}

CANDIDATE PROFILE:
{profile}

JOB POSTING:
Title: {job_title}
Company: {company}
Description: {job_desc}
Job Tier: {job.get('tier', 'unknown')} (1=neuro/dream job, 2=sales eng, 3=ML/CV)
{context}
{match_chat_block}

WRITING RULES — follow these strictly:

1. TONE: Write like Vishal explaining to a smart friend why this role makes sense for him.
   Conversational, technically precise, no corporate language. Use contractions. Use hedges
   where natural ("sort of", "pretty much", "honestly"). Be direct about motivations.

2. STRUCTURE:
   - Opening: What the company/role is doing and why it connects to his actual work history.
     One specific technical thread, not a generic hook. Never open with "I am writing to..."
     or "I was excited to see..." — start with the work itself.
   - Middle: 2-3 concrete things he built or did that are directly relevant. Include enough
     technical detail to be credible. Frame as narrative ("At GTRI, I spent two years..."),
     not bullet points or claims ("I have extensive experience in...").
   - Close: Why the timing makes sense and a low-key, direct call to action. Not "I would
     welcome the opportunity to discuss" — more like "Happy to talk through any of this."

3. THINGS THAT MUST NOT APPEAR:
   - "passionate", "passion", "deeply", "thrive", "leverage", "synergy"
   - "I am confident that", "I believe my background uniquely positions me"
   - "cross-functional collaboration", "drive innovation", "proven track record"
   - "groundbreaking", "transformative", "cutting-edge", "thrilled", "excited"
   - Any sentence that could appear in any other candidate's cover letter unchanged
   - Exclamation marks (zero of them)

4. LENGTH: 3-4 paragraphs, under 350 words. Every sentence earns its place.

5. HONESTY: Do NOT fabricate experiences or skills. If the role asks for something he
   doesn't have, don't address it. Focus on what's genuinely relevant.

6. ROLE-TYPE AWARENESS: This is a Tier 2 (sales/solutions engineering) application.
   Vishal doesn't have formal SE experience, so DO NOT pretend he does. Instead, draw
   the honest parallel: at GTRI he regularly presented technical work to program sponsors,
   translated research outcomes for non-technical stakeholders, and built demos to secure
   continued funding. That IS solutions engineering in a different context. The cover letter
   should acknowledge the career pivot candidly — he's a deep technical engineer who wants
   to be closer to customers and products rather than behind a clearance wall.

Output the cover letter text only, no preamble or sign-off formatting."""

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
