"""
adapters/generic_stagehand.py — Fallback adapter driven by Stagehand's agent loop.

The deterministic adapters (Greenhouse, Lever, Ashby) cover ~70% of postings
job-hunter surfaces. For the rest — Workday, iCIMS, SmartRecruiters, in-house
forms, anything the detector can't confidently place — we hand the form to
Stagehand's `execute` agent loop with a detailed instruction and let it drive.

Trade-offs vs a deterministic adapter:
  + Zero per-ATS maintenance; any form the agent can read, it can try.
  - Higher cost: sh_execute can burn 10-25 tool calls per submission (~$0.50-2).
  - Lower confidence ceiling: we cap recommend=needs_review regardless of agent
    optimism, because the agent has no mechanical guarantee every required
    field was populated correctly. Human-in-the-loop review on the Browserbase
    replay closes that gap.

Never clicks submit — confirm.click_submit_and_verify is the only allowed
path to hitting the submit button. The instruction forbids it explicitly
(rule 5), with redundancy because agents in open-ended form-filling like to
finish the job.
"""

from __future__ import annotations

import logging

from adapters.base import (
    Adapter, FieldFill, FieldSkipped, SubmissionContext, SubmissionResult,
)
from adapters._common import applicant_fields, upload_file
from browser.session import sh_execute, sh_extract
from router import register

logger = logging.getLogger("submitter.adapter.generic")


# Per-submission Stagehand agent budget. 25 steps covers a typical 15-20 field
# Workday / iCIMS form with room for retries; 300s wall-clock is 2x the default
# to account for slower SPAs.
GENERIC_AGENT_MAX_STEPS = 25
GENERIC_AGENT_TIMEOUT_S = 300.0


_INSTRUCTION_TEMPLATE = """\
You are filling out an online job application form on behalf of an applicant.

Applicant profile — use these facts verbatim. Do NOT invent anything not listed.
  Name: {first_name} {last_name}
  Email: {email}
  Phone: {phone}
  Location: {location}
  LinkedIn: {linkedin}
  Website: {website}
  GitHub: {github}
  Current title: {current_title}
  Current company: {current_company}

Effectively-required answers (use when the form asks):
  Work authorization: {work_authorization}
  Need visa sponsorship: {visa_sponsorship_needed}
  Earliest start date: {earliest_start_date}
  Relocation willingness: {relocation_willingness}
  In-person / remote preference: {in_person_willingness}
  AI-usage policy acknowledgement (paste verbatim if asked): {ai_policy_ack}
  Prior interviews: {previous_interview_summary}

Role applying for: {title}

Rules:
  1. Fill every REQUIRED field (asterisked, aria-required, or clearly marked)
     with a value derivable from the applicant profile above.
  2. SKIP demographic / EEO / voluntary-disclosure questions (race, gender,
     veteran status, disability, sexual orientation, "how did you hear",
     name pronunciation, pronouns, hobbies). These are opt-in by policy.
  3. If a required field needs a fact not in the applicant profile above,
     STOP filling that field and note it as missing. Do not guess.
  4. The resume and cover-letter PDFs have already been uploaded via CDP.
     Do not attempt to re-upload them.
  5. DO NOT click the final submit / "Submit application" button. Stop after
     all fillable fields are filled. Clicking submit is handled by a separate
     verification step after this one. If the form spans multiple pages,
     "Next" / "Continue" is allowed, but any button labeled "Submit",
     "Send application", or equivalent is OFF LIMITS.
  6. If you encounter an unexpected error (CAPTCHA, dialog, login wall),
     stop and return what you've done so far — do not attempt to solve it.
"""


_REPORT_SCHEMA: dict = {
    "type": "object",
    "properties": {
        "fields_filled": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "label": {"type": "string"},
                    "value": {"type": "string"},
                },
                "required": ["label", "value"],
            },
        },
        "fields_skipped": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "label":  {"type": "string"},
                    "reason": {"type": "string"},
                },
                "required": ["label", "reason"],
            },
        },
        "missing_required": {
            "type": "array",
            "items": {"type": "string"},
            "description": "Labels of required fields the agent could not confidently fill.",
        },
        "reached_submit_step": {
            "type": "boolean",
            "description": "True iff the form is now parked on the final submit page.",
        },
    },
    "required": ["fields_filled", "fields_skipped", "missing_required", "reached_submit_step"],
}


@register("generic")
class GenericStagehandAdapter(Adapter):
    ats_kind = "generic"

    async def run(self, ctx: SubmissionContext) -> SubmissionResult:
        result = SubmissionResult(adapter_name=self.name)
        sess = ctx.stagehand_session
        page = ctx.page
        app = applicant_fields(ctx.job)

        # 1. Pre-upload resume + cover letter via Playwright so the agent
        #    doesn't have to wrestle with file-drop widgets. upload_file tries
        #    common selectors and records a FieldSkipped on failure rather
        #    than raising — the agent can still run regardless.
        await upload_file(page, result, "resume", str(ctx.resume_pdf_path))
        await upload_file(page, result, "cover_letter", str(ctx.cover_letter_pdf_path))

        # 2. Hand form-filling to the Stagehand agent.
        instruction = _INSTRUCTION_TEMPLATE.format(
            first_name=app["first_name"] or "(not specified)",
            last_name=app["last_name"] or "(not specified)",
            email=app["email"] or "(not specified)",
            phone=app["phone"] or "(not specified)",
            location=app["location"] or "(not specified)",
            linkedin=app["linkedin"] or "(not specified)",
            website=app["website"] or "(not specified)",
            github=app["github"] or "(not specified)",
            current_title=app["current_title"] or "(not specified)",
            current_company=app["current_company"] or "(not specified)",
            work_authorization=app["work_authorization"] or "(not specified)",
            visa_sponsorship_needed=app["visa_sponsorship_needed"] or "(not specified)",
            earliest_start_date=app["earliest_start_date"] or "(not specified)",
            relocation_willingness=app["relocation_willingness"] or "(not specified)",
            in_person_willingness=app["in_person_willingness"] or "(not specified)",
            ai_policy_ack=app["ai_policy_ack"] or "(not specified)",
            previous_interview_summary=app["previous_interview_summary"] or "(not specified)",
            title=ctx.job.get("title", ""),
        )

        try:
            agent_output = await sh_execute(
                sess,
                instruction,
                max_steps=GENERIC_AGENT_MAX_STEPS,
                timeout=GENERIC_AGENT_TIMEOUT_S,
            )
        except Exception as exc:
            logger.exception("generic agent execute failed")
            result.error = f"agent execute failed: {exc}"
            result.recommend = "abort"
            return result

        # Stash the agent's self-reported summary for the review packet — the
        # review UI renders this as the "what the agent thought it did" block.
        if isinstance(agent_output, dict):
            result.agent_reasoning = str(agent_output.get("message") or agent_output)[:4000]
        elif agent_output is not None:
            result.agent_reasoning = str(agent_output)[:4000]

        # 3. Post-hoc structured extraction of what the agent actually touched.
        #    One extra classify-style call in exchange for a machine-readable
        #    fill/skip record the review UI can render.
        try:
            report = await sh_extract(
                sess,
                instruction=(
                    "Examine the job application form in its current state. "
                    "List every field that currently holds a value (label and "
                    "current value), every required field that was intentionally "
                    "left blank (with a reason), and any required field whose "
                    "correct value could not be determined from the applicant "
                    "profile. Also report whether the form is now on its final "
                    "review / submit page."
                ),
                schema=_REPORT_SCHEMA,
                page=page,
                timeout=60.0,
            )
        except Exception as exc:
            logger.warning("generic post-agent report extract failed: %s", exc)
            report = None

        if isinstance(report, dict):
            for f in report.get("fields_filled") or []:
                result.filled_fields.append(FieldFill(
                    label=f.get("label", "?"),
                    value=str(f.get("value", ""))[:500],
                    confidence=0.70,     # agent-reported fills are lower-trust
                    kind="other",
                ))
            for s in report.get("fields_skipped") or []:
                result.skipped_fields.append(FieldSkipped(
                    label=s.get("label", "?"),
                    reason=f"agent skipped: {s.get('reason', '')}",
                ))
            for missing in report.get("missing_required") or []:
                # Prefix must match score_and_recommend's needs_review signal.
                result.skipped_fields.append(FieldSkipped(
                    label=missing,
                    reason="required custom question (agent could not determine value)",
                ))

        # 4. Score. Policy: the generic fallback ALWAYS caps at needs_review
        #    regardless of agent optimism — agent mode has no mechanical
        #    guarantee every required field was filled correctly. A human
        #    review on the Browserbase replay closes that gap.
        n_missing = sum(
            1 for s in result.skipped_fields
            if s.reason.startswith("required custom question")
        )
        if result.error:
            result.recommend = "abort"
        elif n_missing > 0:
            result.confidence = 0.55
            result.recommend = "needs_review"
            result.recommend_reason = (
                f"generic: {n_missing} required field(s) the agent could not fill"
            )
        else:
            # Agent claims success — but we still route to review by default.
            result.confidence = 0.75
            result.recommend = "needs_review"
            result.recommend_reason = (
                "generic: agent reports all fields filled; replay review recommended"
            )

        logger.info(
            "generic: filled=%d skipped=%d confidence=%.2f recommend=%s",
            len(result.filled_fields), len(result.skipped_fields),
            result.confidence, result.recommend,
        )
        return result
