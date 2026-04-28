"""
adapters/ashby.py — Deterministic adapter for Ashby ATS (jobs.ashbyhq.com).

Ashby renders a React SPA with labeled inputs, a file-drop zone for the
resume, and a "Location" field that is usually required (unique to Ashby
among the three deterministic adapters).

Port of the selector knowledge from job-applicant/applicant/ashby.py,
expressed through the Stagehand act/extract abstraction so we get CDP-level
robustness + the shared custom-question handling for free.

Never clicks submit — confirm.click_submit_and_verify does that.
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

logger = logging.getLogger("submitter.adapter.ashby")


# Mandatory-only policy: survey covers core identity + location (Ashby-
# specific core requirement) + resume + cover-letter textarea. LinkedIn /
# website / current-company / current-title are intentionally not surveyed.
_SURVEY_SCHEMA: dict = {
    "type": "object",
    "properties": {
        "full_name_present":            {"type": "boolean"},
        "first_name_present":           {"type": "boolean"},
        "last_name_present":            {"type": "boolean"},
        "email_present":                {"type": "boolean"},
        "phone_present":                {"type": "boolean"},
        "location_present":             {"type": "boolean"},
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

# Location is a hard requirement on most Ashby boards.
_CORE = ("name", "first name", "last name", "email", "phone", "location", "resume")


@register("ashby")
class AshbyAdapter(Adapter):
    ats_kind = "ashby"

    async def run(self, ctx: SubmissionContext) -> SubmissionResult:
        result = SubmissionResult(adapter_name=self.name)
        sess = ctx.stagehand_session
        page = ctx.page
        app = applicant_fields(ctx.job)

        # Ashby is an SPA — give the form a beat to hydrate before we survey.
        try:
            await page.wait_for_load_state("networkidle", timeout=10_000)
        except Exception:
            logger.debug("ashby: networkidle wait timed out; continuing")

        try:
            survey = await sh_extract(
                sess,
                instruction=(
                    "Examine the Ashby application form (a React SPA on "
                    "jobs.ashbyhq.com). Report which labeled core fields "
                    "are present: full name (single) OR first/last, email, "
                    "phone, location, resume upload, and a cover letter "
                    "textarea. Also list any additional custom questions "
                    "with their label, type, and whether they are required."
                ),
                schema=_SURVEY_SCHEMA,
                page=page,
            )
        except Exception as exc:
            logger.exception("ashby survey failed")
            result.error = f"survey failed: {exc}"
            result.recommend = "abort"
            return result

        if not isinstance(survey, dict):
            result.error = f"survey returned non-dict: {type(survey).__name__}"
            result.recommend = "abort"
            return result

        # Name: prefer full_name field if present; else first/last split.
        full_name = app["full_name"] or f"{app['first_name']} {app['last_name']}".strip()
        if survey.get("full_name_present"):
            if full_name:
                await fill_text(sess, page, result, "full name", full_name)
            else:
                result.skipped_fields.append(FieldSkipped(label="name", reason="no applicant name"))
        else:
            await fill_text_if_present(sess, page, result, "first name", app["first_name"], survey.get("first_name_present"))
            await fill_text_if_present(sess, page, result, "last name",  app["last_name"],  survey.get("last_name_present"))
            if not (survey.get("first_name_present") or survey.get("last_name_present")):
                result.skipped_fields.append(FieldSkipped(label="name", reason="no name field on form"))

        await fill_text_if_present(sess, page, result, "email",    app["email"],    survey.get("email_present"))
        await fill_text_if_present(sess, page, result, "phone",    app["phone"],    survey.get("phone_present"))
        await fill_text_if_present(sess, page, result, "location", app["location"], survey.get("location_present"))

        if survey.get("resume_present"):
            await upload_file(page, result, "resume", str(ctx.resume_pdf_path))
        else:
            result.skipped_fields.append(FieldSkipped(label="resume", reason="upload slot not found"))

        await paste_textarea(
            sess, page, result,
            "cover letter",
            ctx.cover_letter_text,
            survey.get("cover_letter_textarea_present"),
        )

        await handle_custom_questions(
            sess, page, result, ctx,
            survey.get("custom_questions") or [],
            ats_name="Ashby",
        )

        score_and_recommend(result, ats_name="ashby", core_labels=_CORE)
        logger.info(
            "ashby: filled=%d skipped=%d confidence=%.2f recommend=%s",
            len(result.filled_fields), len(result.skipped_fields),
            result.confidence, result.recommend,
        )
        return result
