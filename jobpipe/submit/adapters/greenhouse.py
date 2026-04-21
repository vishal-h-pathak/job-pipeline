"""
adapters/greenhouse.py — Deterministic adapter for Greenhouse job boards.

Greenhouse is the happy path. The form DOM is remarkably stable across
boards (`boards.greenhouse.io/*` and `job-boards.greenhouse.io`), so this
adapter avoids calling an LLM for anything except extracting what's on the
page — filling itself is selector-driven via Stagehand act().

Never clicks submit — that's confirm.click_submit_and_verify's job.
"""

from __future__ import annotations

import logging

from adapters.base import Adapter, SubmissionContext, SubmissionResult
from adapters._common import (
    applicant_fields,
    fill_text_if_present,
    handle_custom_question,
    score_and_recommend,
    upload_file,
)
from adapters.base import FieldSkipped
from browser.session import sh_extract
from router import register

logger = logging.getLogger("submitter.adapter.greenhouse")


# Mandatory-only policy: we only survey + fill the core identity fields and
# the file slots. LinkedIn / website / demographic optionals are intentionally
# not surveyed so they can't be auto-filled even if the form asks for them.
_SURVEY_SCHEMA: dict = {
    "type": "object",
    "properties": {
        "first_name_present":    {"type": "boolean"},
        "last_name_present":     {"type": "boolean"},
        "email_present":         {"type": "boolean"},
        "phone_present":         {"type": "boolean"},
        "resume_present":        {"type": "boolean"},
        "cover_letter_present":  {"type": "boolean"},
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

# Core fields whose absence should drop confidence into needs_review.
_CORE = ("first name", "last name", "email", "phone", "resume")


@register("greenhouse")
class GreenhouseAdapter(Adapter):
    ats_kind = "greenhouse"

    async def run(self, ctx: SubmissionContext) -> SubmissionResult:
        result = SubmissionResult(adapter_name=self.name)
        sess = ctx.stagehand_session
        page = ctx.page
        app = applicant_fields(ctx.job)

        # 1. Survey.
        try:
            survey = await sh_extract(
                sess,
                instruction=(
                    "Examine the Greenhouse application form and report which "
                    "standard fields are present (first name, last name, email, "
                    "phone, resume upload, cover letter upload), plus the list "
                    "of additional custom questions below them with each "
                    "question's label, type, and whether it is required."
                ),
                schema=_SURVEY_SCHEMA,
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

        # 2. Core fields. (Mandatory-only: no LinkedIn/website/etc.)
        await fill_text_if_present(sess, page, result, "first name", app["first_name"], survey.get("first_name_present"))
        await fill_text_if_present(sess, page, result, "last name",  app["last_name"],  survey.get("last_name_present"))
        await fill_text_if_present(sess, page, result, "email",      app["email"],      survey.get("email_present"))
        await fill_text_if_present(sess, page, result, "phone",      app["phone"],      survey.get("phone_present"))

        # 3. File uploads.
        if survey.get("resume_present"):
            await upload_file(page, result, "resume", str(ctx.resume_pdf_path))
        else:
            result.skipped_fields.append(FieldSkipped(label="resume", reason="upload slot not found"))
        if survey.get("cover_letter_present"):
            await upload_file(page, result, "cover_letter", str(ctx.cover_letter_pdf_path))
        # Cover letter is optional on most Greenhouse boards — not-found isn't a skip.

        # 4. Custom questions.
        for q in survey.get("custom_questions") or []:
            await handle_custom_question(sess, page, result, ctx, q, ats_name="Greenhouse")

        # 5. Score.
        score_and_recommend(result, ats_name="greenhouse", core_labels=_CORE)
        logger.info(
            "greenhouse: filled=%d skipped=%d confidence=%.2f recommend=%s",
            len(result.filled_fields), len(result.skipped_fields),
            result.confidence, result.recommend,
        )
        return result
