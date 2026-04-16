"""
tailor/cover_letter.py — Claude-powered cover letter generation.

Generates a personalized cover letter for each job application.
"""

import logging
from pathlib import Path
from datetime import datetime

import anthropic
from config import ANTHROPIC_API_KEY, CLAUDE_MODEL, CANDIDATE_PROFILE_PATH, OUTPUT_DIR

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

    prompt = f"""You are writing a cover letter for a job application. The letter should be
professional, specific, and authentic. It should NOT be generic — it must reference
specific details from both the candidate's background and the job posting.

CANDIDATE PROFILE:
{profile}

JOB POSTING:
Title: {job_title}
Company: {company}
Description: {job_desc}
{context}

Write a cover letter that:
1. Opens with a specific, compelling hook (not "I am writing to express my interest")
2. Connects 2-3 specific candidate experiences to the job requirements
3. Shows genuine understanding of the company's mission/product
4. Is concise — 3 paragraphs max, under 400 words total
5. Closes with a clear, confident call to action

Use a natural, confident tone. Avoid cliches and filler.
Do NOT fabricate experiences or skills.

Output the cover letter text only, no preamble."""

    response = client.messages.create(
        model=CLAUDE_MODEL,
        max_tokens=1500,
        messages=[{"role": "user", "content": prompt}],
    )

    cover_letter = response.content[0].text.strip()

    # Save to output directory
    safe_company = "".join(c if c.isalnum() else "_" for c in company)
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    output_path = OUTPUT_DIR / f"cover_letter_{safe_company}_{timestamp}.txt"
    with open(output_path, "w") as f:
        f.write(cover_letter)

    logger.info(f"Cover letter generated for {company} — {job_title}")
    return {
        "cover_letter": cover_letter,
        "output_path": str(output_path),
    }
