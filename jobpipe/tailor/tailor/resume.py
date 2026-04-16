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
from config import ANTHROPIC_API_KEY, CLAUDE_MODEL, CANDIDATE_PROFILE_PATH, OUTPUT_DIR

logger = logging.getLogger("tailor.resume")

client = anthropic.Anthropic(api_key=ANTHROPIC_API_KEY)


def load_candidate_profile() -> str:
    """Load the CLAUDE.md candidate profile."""
    if CANDIDATE_PROFILE_PATH.exists():
        return CANDIDATE_PROFILE_PATH.read_text(encoding="utf-8")
    raise FileNotFoundError(f"Candidate profile not found at {CANDIDATE_PROFILE_PATH}")


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

    prompt = f"""You are a resume tailoring specialist. Given a candidate profile and a job posting,
generate a tailored professional summary and identify which skills and experiences to emphasize.

CANDIDATE PROFILE:
{profile}

JOB POSTING:
Title: {job_title}
Company: {company}
Description: {job_desc}

Respond in JSON format:
{{
    "tailored_summary": "A 3-4 sentence professional summary tailored to this role",
    "emphasis_areas": ["skill or experience to highlight", ...],
    "keywords_to_include": ["specific terms from the job posting to mirror", ...],
    "experience_order": ["which experiences to list first based on relevance", ...],
    "diff_notes": "Brief description of what changed vs the base resume and why"
}}

Be specific. Reference actual experiences from the candidate profile.
Do NOT fabricate experience or skills the candidate doesn't have."""

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

    # Save to output directory
    safe_company = "".join(c if c.isalnum() else "_" for c in company)
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    output_path = OUTPUT_DIR / f"resume_tailoring_{safe_company}_{timestamp}.json"
    with open(output_path, "w") as f:
        json.dump({"job": {
            "id": job.get("id"),
            "title": job_title,
            "company": company,
        }, "tailoring": result}, f, indent=2)

    result["output_path"] = str(output_path)
    logger.info(f"Resume tailored for {company} — {job_title}")
    return result
