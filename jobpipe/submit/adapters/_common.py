"""
adapters/_common.py — Shared primitives for deterministic ATS adapters.

Greenhouse, Lever, and Ashby all follow the same run() skeleton:

    1. extract() a survey of which fields are present.
    2. For each core field (name/email/phone/etc) that's present, try to fill
       it via act(); record filled or skipped with a reason.
    3. For file inputs (resume, cover letter), locate via Playwright and use
       set_input_files() — act() is unreliable for file uploads.
    4. For custom questions, ask Stagehand extract() whether we can confidently
       answer from the applicant profile; fill or skip accordingly.
    5. Score confidence based on whether every required field got filled.

This module holds the pieces of that skeleton that don't vary by ATS. Adapters
still own the survey schema and the ordering, because those are the parts where
ATS quirks matter.
"""

from __future__ import annotations

import logging
from typing import Any

from adapters.base import FieldFill, FieldSkipped, SubmissionContext, SubmissionResult
from browser.session import sh_act, sh_extract

logger = logging.getLogger("submitter.adapter.common")


# ── Applicant profile access ─────────────────────────────────────────────

def applicant_fields(job: dict) -> dict[str, str]:
    """Pull applicant profile values off the job row.

    The tailor writes these into a nested applicant_profile blob; keep this
    helper tolerant of older rows that may have top-level keys instead.
    """
    profile = job.get("applicant_profile") or {}
    def pick(*keys: str) -> str:
        for k in keys:
            v = profile.get(k) or job.get(k)
            if v:
                return str(v)
        return ""

    return {
        "first_name":   pick("first_name", "firstName", "candidate_first_name"),
        "last_name":    pick("last_name", "lastName", "candidate_last_name"),
        "full_name":    pick("full_name", "fullName", "candidate_full_name"),
        "email":        pick("email", "candidate_email"),
        "phone":        pick("phone", "phone_number", "candidate_phone"),
        "linkedin":     pick("linkedin_url", "linkedin", "candidate_linkedin"),
        "website":      pick("website", "portfolio_url", "personal_site"),
        "github":       pick("github", "github_url"),
        "location":     pick("location", "candidate_location", "city"),
        "current_company": pick("current_company", "candidate_company"),
        "current_title":   pick("current_title", "candidate_title", "job_title"),
    }


# ── Text field filling via Stagehand act() ───────────────────────────────

async def fill_text_if_present(
    sess: Any, page: Any, result: SubmissionResult,
    label: str, value: str, present: bool | None,
) -> None:
    """Fill if the survey reported the field AND we have a value to put in it."""
    if not present:
        return
    if not value:
        result.skipped_fields.append(FieldSkipped(label=label, reason="no applicant value"))
        return
    await fill_text(sess, page, result, label, value)


async def fill_text(
    sess: Any, page: Any, result: SubmissionResult,
    label: str, value: str, confidence: float = 0.95,
) -> None:
    """Issue a Stagehand act() that targets a labeled text field."""
    try:
        await sh_act(sess, f"Fill the {label} field with: {value}", page=page)
        result.filled_fields.append(FieldFill(label=label, value=value, confidence=confidence))
    except Exception as exc:
        logger.warning("fill '%s' failed: %s", label, exc)
        result.skipped_fields.append(FieldSkipped(label=label, reason=f"fill failed: {exc}"))


# ── File upload via Playwright set_input_files ───────────────────────────

_FILE_SELECTOR_PRESETS: dict[str, list[str]] = {
    "resume": [
        "input[type=file][name*='resume' i]",
        "input[type=file][id*='resume' i]",
        "input[type=file][aria-label*='resume' i]",
        "input[type=file][accept*='pdf' i]",
    ],
    "cover_letter": [
        "input[type=file][name*='cover' i]",
        "input[type=file][id*='cover' i]",
        "input[type=file][aria-label*='cover' i]",
    ],
}


async def upload_file(
    page: Any, result: SubmissionResult,
    label: str, local_path: str,
) -> None:
    """Upload a file by finding the first matching <input type=file>.

    Tries label-specific selectors first, then falls back to the first file
    input on the page. Logs the failure as a FieldSkipped entry — never
    raises, so a single upload failure never aborts the adapter.
    """
    selectors = _FILE_SELECTOR_PRESETS.get(label) or ["input[type=file]"]
    # Always include a generic fallback as the last resort.
    if "input[type=file]" not in selectors:
        selectors = [*selectors, "input[type=file]"]

    try:
        locator = None
        for sel in selectors:
            cand = page.locator(sel)
            if await cand.count() > 0:
                locator = cand.first
                break
        if locator is None:
            raise RuntimeError(f"no file input found for {label}")

        await locator.set_input_files(local_path)
        result.filled_fields.append(
            FieldFill(label=label, value=local_path, confidence=0.98, kind="file")
        )
    except Exception as exc:
        logger.warning("upload '%s' failed: %s", label, exc)
        result.skipped_fields.append(FieldSkipped(label=label, reason=f"upload failed: {exc}"))


# ── Textarea paste (for cover-letter-body fields on Lever/Ashby) ─────────

async def paste_textarea(
    sess: Any, page: Any, result: SubmissionResult,
    label: str, value: str, present: bool | None,
) -> None:
    """Paste into a rich-text or textarea field via Stagehand act()."""
    if not present or not value:
        if present and not value:
            result.skipped_fields.append(FieldSkipped(label=label, reason="no applicant text"))
        return
    try:
        # Cap the pasted length at a reasonable bound — ATSes often limit to
        # 5000 chars for cover letter bodies.
        snippet = value if len(value) <= 5000 else value[:5000]
        await sh_act(sess, f"Paste the following text into the {label} field:\n\n{snippet}", page=page)
        result.filled_fields.append(FieldFill(label=label, value=f"<{len(snippet)} chars>", confidence=0.90, kind="textarea"))
    except Exception as exc:
        logger.warning("paste '%s' failed: %s", label, exc)
        result.skipped_fields.append(FieldSkipped(label=label, reason=f"paste failed: {exc}"))


# ── Custom question handling ─────────────────────────────────────────────

CUSTOM_Q_ANSWER_SCHEMA: dict = {
    "type": "object",
    "properties": {
        "answer":   {"type": "string"},
        "decision": {
            "type": "string",
            "enum": ["answer", "skip"],
            "description": "answer if the question can be confidently answered from the applicant profile; skip otherwise",
        },
        "reason":   {"type": "string"},
    },
    "required": ["decision"],
}


async def handle_custom_question(
    sess: Any, page: Any, result: SubmissionResult,
    ctx: SubmissionContext, q: dict, *, ats_name: str,
) -> None:
    """Best-effort custom-question filler — mandatory-only policy.

    Policy (per Vishal, 2026-04): only required custom questions are ever
    answered. Optional questions (e.g. "how to pronounce your name",
    "favorite hobby", pronouns, demographic self-ID) are unconditionally
    skipped before any LLM call — both to keep runs fast and to keep the
    submitted application surface minimal.

    File uploads are never auto-answered regardless of required-ness.
    Required non-file questions go through Stagehand extract() with the
    applicant profile context; the model returns an answer-or-skip
    decision which we honor.
    """
    label = q.get("label", "?")
    kind = q.get("kind", "text")
    required = bool(q.get("required"))

    # Policy: skip every optional question before burning an LLM call.
    if not required:
        result.skipped_fields.append(
            FieldSkipped(label=label, reason="optional custom question (policy: required-only)")
        )
        return

    if kind == "file":
        result.skipped_fields.append(
            FieldSkipped(label=label, reason="required custom question (file upload)")
        )
        return

    try:
        decision = await sh_extract(
            sess,
            instruction=(
                f"Given this custom application question from a {ats_name} form:\n"
                f"Q: {label}\n"
                f"Type: {kind}. Required: {required}.\n"
                f"Applicant context:\n"
                f"  Title: {ctx.job.get('title', '')}\n"
                f"  Cover letter (first 400 chars): {ctx.cover_letter_text[:400]}\n"
                f"Decide: answer if-and-only-if the applicant profile supports "
                f"a confident, truthful answer; otherwise skip."
            ),
            schema=CUSTOM_Q_ANSWER_SCHEMA,
            page=page,
        )
    except Exception as exc:
        logger.warning("decision for '%s' failed: %s", label, exc)
        reason_prefix = "required custom question" if required else "optional custom question"
        result.skipped_fields.append(FieldSkipped(label=label, reason=f"{reason_prefix} (decision failed: {exc})"))
        return

    if (
        not isinstance(decision, dict)
        or decision.get("decision") != "answer"
        or not decision.get("answer")
    ):
        reason_prefix = "required custom question" if required else "optional custom question"
        detail = (decision or {}).get("reason", "no confident answer") if isinstance(decision, dict) else "malformed response"
        result.skipped_fields.append(FieldSkipped(label=label, reason=f"{reason_prefix} ({detail})"))
        return

    answer = decision["answer"]
    try:
        await sh_act(sess, f"Answer the question '{label}' with: {answer}", page=page)
        result.filled_fields.append(FieldFill(
            label=label, value=answer, confidence=0.85, kind=kind or "text",
        ))
    except Exception as exc:
        reason_prefix = "required custom question" if required else "optional custom question"
        result.skipped_fields.append(FieldSkipped(label=label, reason=f"{reason_prefix} (act failed: {exc})"))


# ── Confidence scoring ───────────────────────────────────────────────────

def score_and_recommend(
    result: SubmissionResult,
    *,
    ats_name: str,
    core_labels: tuple[str, ...],
) -> None:
    """Mutate `result.confidence` / `.recommend` / `.recommend_reason` based on
    what got filled versus what got skipped with a "required" reason.

    Policy:
      - any core label in skipped → 0.70 needs_review
      - any "required custom question" in skipped → 0.70 needs_review
      - any optional skip         → 0.90 auto_submit (softer)
      - nothing skipped           → 0.95 auto_submit
    """
    required_customs_missing = [
        s for s in result.skipped_fields
        if s.reason.startswith("required custom question")
    ]
    core_missing = [s for s in result.skipped_fields if s.label in core_labels]

    if required_customs_missing or core_missing:
        result.confidence = 0.70
        result.recommend = "needs_review"
        result.recommend_reason = (
            f"{ats_name}: {len(core_missing)} core + "
            f"{len(required_customs_missing)} required-custom fields unfilled"
        )
    elif result.skipped_fields:
        result.confidence = 0.90
        result.recommend = "auto_submit"
        result.recommend_reason = f"{ats_name}: all required filled; some optionals skipped"
    else:
        result.confidence = 0.95
        result.recommend = "auto_submit"
        result.recommend_reason = f"{ats_name}: all fields filled"
