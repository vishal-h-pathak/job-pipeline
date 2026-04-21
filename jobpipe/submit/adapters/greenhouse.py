"""
adapters/greenhouse.py — Deterministic adapter for Greenhouse job boards.

Greenhouse is the happy path. The form DOM is remarkably stable across
boards (`boards.greenhouse.io/*` and the embedded `job-boards.greenhouse.io`
iframe), so this adapter avoids calling an LLM for anything except extracting
what's on the page — filling itself is selector-driven via Stagehand act().

Flow:
    1. Land on the application form (open_session already did page.goto).
    2. Extract the list of visible fields so we know what to fill.
    3. Fill name / email / phone / LinkedIn / resume / cover letter.
    4. Walk any custom questions, filling the ones we can map, skipping the
       rest with a reason.
    5. Screenshot the pre-submit state for the review packet.
    6. Set confidence: 0.95 if every *required* field is filled, else 0.80.
    7. Return — DO NOT click submit. confirm.click_submit_and_verify does that.

The adapter never raises for form-shape surprises; it logs them as
FieldSkipped entries and lowers confidence so the human gets the last word.
"""

from __future__ import annotations

import logging
from typing import Any

from adapters.base import (
    Adapter,
    FieldFill,
    FieldSkipped,
    SubmissionContext,
    SubmissionResult,
)
from browser.session import sh_act, sh_extract, sh_observe
from router import register

logger = logging.getLogger("submitter.adapter.greenhouse")


# Canonical Greenhouse required-field set across ~99% of boards. Custom
# questions live below this and we handle them separately.
_CORE_FIELD_SCHEMA: dict = {
    "type": "object",
    "properties": {
        "first_name_present":    {"type": "boolean"},
        "last_name_present":     {"type": "boolean"},
        "email_present":         {"type": "boolean"},
        "phone_present":         {"type": "boolean"},
        "resume_present":        {"type": "boolean"},
        "cover_letter_present":  {"type": "boolean"},
        "linkedin_present":      {"type": "boolean"},
        "website_present":       {"type": "boolean"},
        "custom_questions": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "label":    {"type": "string"},
                    "kind":     {"type": "string", "description": "one of: text, textarea, select, radio, checkbox, file"},
                    "required": {"type": "boolean"},
                },
                "required": ["label", "kind", "required"],
            },
        },
    },
    "required": [
        "first_name_present", "last_name_present", "email_present",
        "phone_present", "resume_present", "custom_questions",
    ],
}


@register("greenhouse")
class GreenhouseAdapter(Adapter):
    ats_kind = "greenhouse"

    async def run(self, ctx: SubmissionContext) -> SubmissionResult:
        result = SubmissionResult(adapter_name=self.name)
        sess = ctx.stagehand_session
        page = ctx.page
        applicant = _applicant_fields(ctx.job)

        # 1. Survey the form.
        try:
            survey = await sh_extract(
                sess,
                instruction=(
                    "Examine the Greenhouse application form and report which "
                    "standard fields are present (first name, last name, email, "
                    "phone, resume upload, cover letter upload, LinkedIn URL, "
                    "personal website), plus the list of additional custom "
                    "questions below them with each question's label and type."
                ),
                schema=_CORE_FIELD_SCHEMA,
                page=page,
            )
        except Exception as exc:
            logger.exception("greenhouse survey failed")
            result.error = f"survey failed: {exc}"
            result.recommend = "abort"
            return result

        if not isinstance(survey, dict):
            result.error = f"survey returned non-dict: {type(survey).__name__}"
            result.recommend = "abort"
            return result

        # 2. Fill core fields.
        await _fill_text_if_present(sess, page, result, "first name",
                                    applicant["first_name"], survey.get("first_name_present"))
        await _fill_text_if_present(sess, page, result, "last name",
                                    applicant["last_name"],  survey.get("last_name_present"))
        await _fill_text_if_present(sess, page, result, "email",
                                    applicant["email"],      survey.get("email_present"))
        await _fill_text_if_present(sess, page, result, "phone",
                                    applicant["phone"],      survey.get("phone_present"))

        if survey.get("linkedin_present") and applicant["linkedin"]:
            await _fill_text(sess, page, result, "linkedin", applicant["linkedin"])
        if survey.get("website_present") and applicant["website"]:
            await _fill_text(sess, page, result, "website", applicant["website"])

        # 3. File uploads via Playwright — Stagehand act() is unreliable for
        # <input type="file">, so we locate the input and call set_input_files
        # directly. Greenhouse consistently labels these.
        if survey.get("resume_present"):
            await _upload_file(page, result, "resume", str(ctx.resume_pdf_path))
        else:
            result.skipped_fields.append(FieldSkipped(label="resume", reason="upload slot not found"))

        if survey.get("cover_letter_present"):
            await _upload_file(page, result, "cover_letter", str(ctx.cover_letter_pdf_path))
        # Cover letter is often optional on Greenhouse; not-found isn't a skip.

        # 4. Custom questions — best-effort mapping against applicant profile,
        # skip unmapped with a reason so the review UI can display them.
        for q in survey.get("custom_questions") or []:
            await _handle_custom_question(sess, page, result, ctx, q)

        # 5. Final confidence. Any required custom question we skipped is a
        # hard floor on confidence so the run gets routed to review.
        required_fill_missing = [
            s for s in result.skipped_fields
            if s.reason.startswith("required custom question")
        ]
        core_fill_missing = [
            s for s in result.skipped_fields
            if s.label in ("first name", "last name", "email", "phone", "resume")
        ]

        if required_fill_missing or core_fill_missing:
            result.confidence = 0.70
            result.recommend = "needs_review"
            result.recommend_reason = (
                f"greenhouse: {len(core_fill_missing)} core + "
                f"{len(required_fill_missing)} required-custom fields unfilled"
            )
        elif result.skipped_fields:
            # Skipped optionals only — still auto-submittable, just softer.
            result.confidence = 0.90
            result.recommend = "auto_submit"
            result.recommend_reason = "greenhouse: all required filled; some optionals skipped"
        else:
            result.confidence = 0.95
            result.recommend = "auto_submit"
            result.recommend_reason = "greenhouse: all fields filled"

        logger.info(
            "greenhouse: filled=%d skipped=%d confidence=%.2f recommend=%s",
            len(result.filled_fields), len(result.skipped_fields),
            result.confidence, result.recommend,
        )
        return result


# ── Private helpers ──────────────────────────────────────────────────────

def _applicant_fields(job: dict) -> dict[str, str]:
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
        "first_name": pick("first_name", "firstName"),
        "last_name":  pick("last_name", "lastName"),
        "email":      pick("email"),
        "phone":      pick("phone", "phone_number"),
        "linkedin":   pick("linkedin_url", "linkedin"),
        "website":    pick("website", "portfolio_url", "personal_site"),
    }


async def _fill_text_if_present(
    sess: Any, page: Any, result: SubmissionResult,
    label: str, value: str, present: bool | None,
) -> None:
    if not present:
        return
    if not value:
        result.skipped_fields.append(FieldSkipped(label=label, reason="no applicant value"))
        return
    await _fill_text(sess, page, result, label, value)


async def _fill_text(
    sess: Any, page: Any, result: SubmissionResult,
    label: str, value: str,
) -> None:
    try:
        await sh_act(sess, f"Fill the {label} field with: {value}", page=page)
        result.filled_fields.append(FieldFill(label=label, value=value, confidence=0.95))
    except Exception as exc:
        logger.warning("fill '%s' failed: %s", label, exc)
        result.skipped_fields.append(FieldSkipped(label=label, reason=f"fill failed: {exc}"))


async def _upload_file(
    page: Any, result: SubmissionResult,
    label: str, local_path: str,
) -> None:
    """Upload a PDF via Playwright set_input_files, scoped by input label.

    Greenhouse renders file inputs as hidden `<input type="file">` under
    labeled buttons ("Attach Resume"). We try a stable selector set first,
    then fall back to any matching file input.
    """
    try:
        selectors = []
        if label == "resume":
            selectors = [
                "input[type=file][name*='resume' i]",
                "input[type=file][id*='resume' i]",
                "input[type=file][aria-label*='resume' i]",
            ]
        elif label == "cover_letter":
            selectors = [
                "input[type=file][name*='cover' i]",
                "input[type=file][id*='cover' i]",
                "input[type=file][aria-label*='cover' i]",
            ]
        else:
            selectors = ["input[type=file]"]

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


_CUSTOM_Q_ANSWER_SCHEMA: dict = {
    "type": "object",
    "properties": {
        "answer": {"type": "string"},
        "decision": {
            "type": "string",
            "enum": ["answer", "skip"],
            "description": "answer if the question can be confidently answered from the applicant profile; skip otherwise",
        },
        "reason": {"type": "string"},
    },
    "required": ["decision"],
}


async def _handle_custom_question(
    sess: Any, page: Any, result: SubmissionResult,
    ctx: SubmissionContext, q: dict,
) -> None:
    """Attempt to answer a custom question; skip with a documented reason
    if we can't. Required questions with no answer float the job to review.
    """
    label = q.get("label", "?")
    kind = q.get("kind", "text")
    required = bool(q.get("required"))

    # File-type custom questions (extra portfolio uploads etc.) are always
    # skipped — the review queue handles manual uploads.
    if kind == "file":
        reason = "required custom question (file upload)" if required else "optional file-upload custom question"
        result.skipped_fields.append(FieldSkipped(label=label, reason=reason))
        return

    try:
        decision = await sh_extract(
            sess,
            instruction=(
                f"Given this custom application question from a Greenhouse form:\n"
                f"Q: {label}\n"
                f"Type: {kind}. Required: {required}.\n"
                f"And the applicant context (job title, cover letter text):\n"
                f"Title: {ctx.job.get('title', '')}\n"
                f"Cover letter (first 400 chars): {ctx.cover_letter_text[:400]}\n"
                f"Decide: answer if-and-only-if the applicant profile supports "
                f"a confident, truthful answer; otherwise skip."
            ),
            schema=_CUSTOM_Q_ANSWER_SCHEMA,
            page=page,
        )
    except Exception as exc:
        logger.warning("decision for '%s' failed: %s", label, exc)
        reason_prefix = "required custom question" if required else "optional custom question"
        result.skipped_fields.append(FieldSkipped(label=label, reason=f"{reason_prefix} (decision failed: {exc})"))
        return

    if not isinstance(decision, dict) or decision.get("decision") != "answer" or not decision.get("answer"):
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
