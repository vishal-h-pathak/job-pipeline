"""
adapters/lever.py — Deterministic adapter for Lever ATS (jobs.lever.co).

╔══════════════════════════════════════════════════════════════════════╗
║  LEGACY (Path B). Stagehand + Browserbase Lever adapter, retired     ║
║  during the local-Playwright consolidation. The live Lever handler   ║
║  is ``submit/adapters/prepare_dom/lever.py`` (sync Playwright). Do   ║
║  not extend this module — extend the prepare_dom one instead.        ║
╚══════════════════════════════════════════════════════════════════════╝

Lever's application form is structurally similar to Greenhouse's but with
two notable quirks:

  - A single "full name" field instead of first + last on some boards
    (respects the board's config — so we survey BOTH and fill whichever
    the form actually renders).
  - Cover letter is almost always an optional textarea body (not a file
    upload) labelled "Additional information" or "Cover letter".

Never clicks submit — that's confirm.click_submit_and_verify's job.
"""

from __future__ import annotations

import logging

from adapters.base import Adapter, FieldSkipped, SubmissionContext, SubmissionResult
from adapters._common import (
    applicant_fields,
    fill_text_if_present,
    fill_text,
    handle_custom_questions,
    paste_textarea,
    score_and_recommend,
    upload_file,
)
from browser.session import sh_extract
from router import register

logger = logging.getLogger("submitter.adapter.lever")


# Mandatory-only policy: survey covers only the core identity fields, the
# resume slot, and the cover-letter textarea. LinkedIn / GitHub / website /
# current-company are intentionally not surveyed or filled.
_SURVEY_SCHEMA: dict = {
    "type": "object",
    "properties": {
        "full_name_present":            {"type": "boolean"},
        "first_name_present":           {"type": "boolean"},
        "last_name_present":            {"type": "boolean"},
        "email_present":                {"type": "boolean"},
        "phone_present":                {"type": "boolean"},
        "resume_present":               {"type": "boolean"},
        "cover_letter_textarea_present":{"type": "boolean"},
        "custom_questions": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "label":    {"type": "string"},
                    "kind":     {"type": "string"},
                    "required": {"type": "boolean"},
                },
                "required": ["label", "kind", "required"],
            },
        },
    },
    "required": ["email_present", "resume_present", "custom_questions"],
}

# On Lever the name field is one-or-the-other, and we treat "name coverage"
# as the core requirement rather than both first+last. "name" is used as the
# skip-label whenever neither full_name nor first_name/last_name can be filled.
_CORE = ("name", "first name", "last name", "email", "phone", "resume")


@register("lever")
class LeverAdapter(Adapter):
    ats_kind = "lever"

    async def run(self, ctx: SubmissionContext) -> SubmissionResult:
        result = SubmissionResult(adapter_name=self.name)
        sess = ctx.stagehand_session
        page = ctx.page
        app = applicant_fields(ctx.job)

        try:
            survey = await sh_extract(
                sess,
                instruction=(
                    "Examine the Lever application form and report which "
                    "core fields are present: full name (single field) OR "
                    "first/last name, email, phone, resume upload, and a "
                    "cover letter textarea (often labelled 'Additional "
                    "information' or similar). Plus list any additional "
                    "custom questions with their label, type, and whether "
                    "they are required."
                ),
                schema=_SURVEY_SCHEMA,
                page=page,
            )
        except Exception as exc:
            logger.exception("lever survey failed")
            result.error = f"survey failed: {exc}"
            result.recommend = "abort"
            return result

        if not isinstance(survey, dict):
            result.error = f"survey returned non-dict: {type(survey).__name__}"
            result.recommend = "abort"
            return result

        # Name: prefer full name if that's what the form has; else first+last.
        full_name = app["full_name"] or f"{app['first_name']} {app['last_name']}".strip()
        if survey.get("full_name_present"):
            if full_name:
                await fill_text(sess, page, result, "full name", full_name)
            else:
                result.skipped_fields.append(FieldSkipped(label="name", reason="no applicant name"))
        else:
            await fill_text_if_present(sess, page, result, "first name", app["first_name"], survey.get("first_name_present"))
            await fill_text_if_present(sess, page, result, "last name",  app["last_name"],  survey.get("last_name_present"))
            if not survey.get("first_name_present") and not survey.get("last_name_present") and not survey.get("full_name_present"):
                result.skipped_fields.append(FieldSkipped(label="name", reason="no name field found on form"))

        await fill_text_if_present(sess, page, result, "email", app["email"], survey.get("email_present"))
        await fill_text_if_present(sess, page, result, "phone", app["phone"], survey.get("phone_present"))

        # Resume: required. Missing input slot is a hard skip that drops into review.
        if survey.get("resume_present"):
            await upload_file(page, result, "resume", str(ctx.resume_pdf_path))
        else:
            result.skipped_fields.append(FieldSkipped(label="resume", reason="upload slot not found"))

        # Cover letter on Lever is typically a textarea paste.
        await paste_textarea(
            sess, page, result,
            "cover letter (additional information)",
            ctx.cover_letter_text,
            survey.get("cover_letter_textarea_present"),
        )

        await handle_custom_questions(
            sess, page, result, ctx,
            survey.get("custom_questions") or [],
            ats_name="Lever",
        )

        score_and_recommend(result, ats_name="lever", core_labels=_CORE)
        logger.info(
            "lever: filled=%d skipped=%d confidence=%.2f recommend=%s",
            len(result.filled_fields), len(result.skipped_fields),
            result.confidence, result.recommend,
        )
        return result
