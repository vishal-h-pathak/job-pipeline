"""tailor/ats_qa.py — Post-tailor ATS keyword gate + humanizer (P2).

One Sonnet-class call over (JD, rendered resume text) run after the
resume is rendered and before ``mark_ready_for_review``. Adoption of
"The AI Hiring Machine" doc's ATS Scorecard + Humanizer modules (see
reviews/REVIEW_2026-07-13_hiring_machine_consolidation.md §2, items 2
and 4), folded into the single call the doc's own module list keeps
separate — jobpipe already pays for one LLM round-trip per JD, no need
for two.

Never auto-applies anything: the JSON this produces is stored on the
row (``jobs.ats_qa``) for the human to read at the existing
materials-review gate. ``run_ats_qa`` also runs a deterministic
never-fabricate guard over the model's ``robotic_bullets`` before
returning — a rewrite that drops a real number from the original bullet
or references a bullet that isn't actually in the resume is dropped
rather than trusted.
"""

from __future__ import annotations

import json
import logging
import re

from jobpipe.config import TAILOR_CLAUDE_MODEL as CLAUDE_MODEL
from jobpipe.shared import llm
from prompts import cached_system_blocks, load_task_prompt

logger = logging.getLogger("tailor.ats_qa")

_NUMBER_RE = re.compile(r"\d[\d,.]*%?")


def _extract_json(text: str) -> dict:
    text = text.strip()
    text = re.sub(r"^```(?:json)?|```$", "", text, flags=re.MULTILINE).strip()
    start = text.find("{")
    end = text.rfind("}")
    if start == -1 or end == -1:
        raise ValueError(f"no JSON object in response: {text!r}")
    return json.loads(text[start : end + 1])


def resume_text_from_tailored(tailored: dict) -> str:
    """Flatten the final (post-trim) tailored resume dict — the same
    structure ``generate_tailored_latex()`` compiled into the PDF —
    into plain text for the QA prompt.

    Mirrors the sections the LaTeX renderer draws from
    (``latex_resume.py``): summary, skills, and each employer's bullets.
    Not meant to be a faithful re-render, just enough surface for a
    keyword/robotic-bullet pass.
    """
    if not tailored:
        return ""
    lines: list[str] = []
    summary = tailored.get("tailored_summary") or tailored.get("summary")
    if summary:
        lines.append(str(summary))

    skills = tailored.get("skills") or {}
    if isinstance(skills, dict):
        for category, items in skills.items():
            if isinstance(items, (list, tuple)):
                lines.append(f"{category}: {', '.join(str(i) for i in items)}")
            elif items:
                lines.append(f"{category}: {items}")

    for org in tailored.get("experience") or []:
        title = org.get("title") or ""
        org_name = org.get("org") or ""
        if title or org_name:
            lines.append(f"{title} — {org_name}".strip(" —"))
        for project in org.get("projects") or []:
            for bullet in project.get("bullets") or []:
                lines.append(f"- {bullet}")

    for project in tailored.get("projects") or []:
        for bullet in project.get("bullets") or []:
            lines.append(f"- {bullet}")

    return "\n".join(str(line) for line in lines if line)


def _validate_robotic_bullets(resume_text: str, robotic_bullets: list) -> list:
    """Never-fabricate guard. Drops any proposed rewrite that either:

    - references a ``bullet`` that isn't actually present in the
      rendered resume (the model inventing a bullet to "fix"), or
    - drops a number that was present in the original bullet (the
      model quietly discarding a real metric while "humanizing").

    Returns only the entries that survive both checks.
    """
    validated: list = []
    for entry in robotic_bullets or []:
        if not isinstance(entry, dict):
            continue
        bullet = str(entry.get("bullet") or "").strip()
        rewrite = str(entry.get("humanized_rewrite") or "").strip()
        if not bullet or not rewrite:
            continue
        if bullet not in resume_text:
            logger.warning(
                "ats_qa: dropping robotic_bullet not found verbatim in resume: %r",
                bullet[:80],
            )
            continue
        original_numbers = set(_NUMBER_RE.findall(bullet))
        rewrite_numbers = set(_NUMBER_RE.findall(rewrite))
        if not original_numbers.issubset(rewrite_numbers):
            logger.warning(
                "ats_qa: dropping robotic_bullet rewrite that lost a number: %r -> %r",
                bullet[:80], rewrite[:80],
            )
            continue
        validated.append({"bullet": bullet, "humanized_rewrite": rewrite})
    return validated


def run_ats_qa(job: dict, resume_text: str) -> dict:
    """Score the rendered resume against the JD and flag robotic bullets.

    Args:
        job: Job row dict — reads ``title``/``company``/``description``.
        resume_text: Flattened resume text (see
            :func:`resume_text_from_tailored`).

    Returns:
        ``{top_keywords, missing, ats_score, highest_impact_fix,
        robotic_bullets}`` — always this shape, normalized and
        never-fabricate-guarded, even on a malformed model response
        (falls back to empty/zero values rather than raising, since a
        QA-step hiccup must never block ``mark_ready_for_review``).
    """
    job_title = job.get("title", "Unknown")
    company = job.get("company", "Unknown")
    job_desc = job.get("description", "")

    prompt = load_task_prompt(
        "ats_qa",
        job_title=job_title,
        company=company,
        job_desc=job_desc,
        resume_text=resume_text,
    )

    try:
        response_text = llm.complete(
            system=cached_system_blocks(),
            prompt=prompt,
            model=CLAUDE_MODEL,
            max_tokens=2000,
        )
        result = _extract_json(response_text)
    except Exception as exc:
        logger.warning("ats_qa: QA call failed for %s — %s: %s", company, job_title, exc)
        result = {}

    top_keywords = [str(k) for k in (result.get("top_keywords") or [])][:15]
    missing = [str(k) for k in (result.get("missing") or [])]
    try:
        ats_score = int(result.get("ats_score", 0))
    except (TypeError, ValueError):
        ats_score = 0
    ats_score = max(0, min(100, ats_score))
    highest_impact_fix = str(result.get("highest_impact_fix") or "").strip()
    robotic_bullets = _validate_robotic_bullets(
        resume_text, result.get("robotic_bullets") or []
    )

    return {
        "top_keywords": top_keywords,
        "missing": missing,
        "ats_score": ats_score,
        "highest_impact_fix": highest_impact_fix,
        "robotic_bullets": robotic_bullets,
    }
