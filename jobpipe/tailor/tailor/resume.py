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
from config import ANTHROPIC_API_KEY, CLAUDE_MODEL, CANDIDATE_PROFILE_PATH

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

    # Load voice profile if available
    voice_profile = ""
    voice_path = Path(__file__).parent.parent / "templates" / "VOICE_PROFILE.md"
    if voice_path.exists():
        voice_profile = voice_path.read_text(encoding="utf-8")

    prompt = f"""You are tailoring a resume for Vishal Pathak. The resume must be strictly
professional — no personal statements, no interests section, no "passionate about" language.
Every bullet should describe something concrete that was built, deployed, or shipped.

VOICE PROFILE (for tone of summary only):
{voice_profile}

CANDIDATE PROFILE:
{profile}

JOB POSTING:
Title: {job_title}
Company: {company}
Description: {job_desc}
Job Tier: {job.get('tier', 'unknown')} (1=neuro/dream job, 2=sales eng, 3=ML/CV)

RESUME RULES — follow these strictly:

1. SUMMARY: 2-3 sentences max. Written in Vishal's voice — technically precise, no fluff.
   Lead with what he does (engineer), the domain (neuro/ML/embedded), and years of experience.
   Do not include "passionate", "driven", or any soft descriptors.
   Example tone: "Electrical engineer with 7+ years across neuromorphic hardware, spiking neural
   networks, and embedded ML. Most recent work at GTRI deploying SNNs on Intel Loihi and
   real-time detection models on Jetson Orin."

2. EMPHASIS AREAS: Pick the 3-5 skills/experiences from his background that most directly
   match the job requirements. Only include things he actually has.

3. KEYWORDS: Mirror specific terms from the job posting that Vishal genuinely has experience
   with. If the posting says "computer vision" and he did RT-DETR, include it. If the posting
   says "Kubernetes" and he hasn't used it, do NOT include it.

4. EXPERIENCE ORDER: Reorder his roles so the most relevant work appears first. Always
   include both GTRI and Rain Neuromorphics. Personal projects (FlyGym, trading agent) can
   be included in a "Projects" section if they're relevant to the role.

5. BULLET STYLE: Each experience bullet should follow the pattern:
   [Action verb] + [specific thing built/done] + [tools/tech used] + [measurable outcome if available]
   Example: "Deployed spiking neural networks on Intel Kapoho Bay for low-power object detection,
   achieving 3x power reduction vs GPU baseline while preserving 94% mAP accuracy"

6. DO NOT fabricate experiences, skills, certifications, or metrics he doesn't have.

7. TIER 2 FRAMING (for sales/solutions engineering roles): Vishal has no formal SE title,
   but he has relevant experience: presenting to DoD program sponsors, writing technical
   proposals, translating complex research into stakeholder-friendly deliverables, and
   building demos for non-technical decision-makers. Frame these GTRI experiences through
   an SE lens — they ARE pre-sales/post-sales activities, just in a government context.
   Also emphasize: Python proficiency, ability to build technical demos rapidly, comfort
   with customer-facing communication, and his autonomous AI agent projects (trading agent,
   job-hunter) which demonstrate full-stack product-minded engineering.

Respond in JSON format:
{{
    "tailored_summary": "2-3 sentence summary in Vishal's voice",
    "emphasis_areas": ["specific skill or experience area to highlight"],
    "keywords_to_include": ["job posting terms that genuinely match his background"],
    "experience_order": ["role/org to list first, second, etc."],
    "suggested_bullets": {{
        "GTRI": ["rewritten bullet 1", "rewritten bullet 2", ...],
        "Rain Neuromorphics": ["rewritten bullet 1", ...],
        "Projects": ["optional relevant project bullets"]
    }},
    "skills_section": {{
        "category_name": ["skill1", "skill2", ...]
    }},
    "diff_notes": "Brief description of what changed from a general resume and why"
}}'"""

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

    logger.info(f"Resume tailored for {company} — {job_title}")
    return result
