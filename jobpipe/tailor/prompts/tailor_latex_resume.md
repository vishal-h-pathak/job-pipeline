# Tailor LaTeX Resume

You are tailoring a LaTeX resume for Vishal Pathak for a specific job
application. You have his complete base resume data below. Your job is
to SELECT and REORDER content to best match the target role. You may
rewrite bullet points to emphasize relevant aspects, but you MUST NOT
fabricate experience, skills, or projects he doesn't have.

VOICE PROFILE:
{voice_profile}

CANDIDATE PROFILE:
{profile}

BASE RESUME DATA (this is the complete truth — all projects and bullets available):
{base_resume_json}

TAILORING GUIDANCE (from earlier analysis):
{tailoring_json}

TARGET JOB:
Title: {job_title}
Company: {company}
Description: {job_desc}
{match_chat_block}

CHOSEN ARCHETYPE (J-4 — bias project selection + skill ordering toward
this lane. Same candidate, different framing):
{archetype_block}

YOUR TASK — respond with a JSON object containing:

1. "skills" — a dict of 4-5 skill categories with comma-separated skills.
   Rewrite category names and reorder skills to lead with what's most relevant.
   Only include skills he actually has from the base data.

2. "experience" — a list of experience entries. Each entry has:
   - "org", "title", "location", "period" (keep these factual)
   - "projects" — list of projects to INCLUDE (you can drop irrelevant ones).
     Each project has "name" (null for Rain), "period", and "bullets".
     You may rewrite bullets to emphasize relevant aspects, but keep them factual.
     Lead with the most relevant projects for this role.

3. "summary_line" — optional 1-line summary to add below the header (or null to skip).
   If included, write it in Vishal's voice: direct, technical, no fluff.

RULES:
- GTRI projects you can include or exclude based on relevance. Always include at least
  SPARSE and one other. Drop projects that add no value for this specific role.
- Rain Neuromorphics should always be included.
- Rewrite skill categories to match the job posting's language where honest.
- Bullets should be specific and technical. No vague claims.
- Keep the resume to 1 page worth of content (roughly 15-20 bullets total max).
- Do NOT add projects, employers, or skills that don't exist in the base data.

Respond with valid JSON only, no markdown.
