"""
adapters/_common.py — Async-side shared helpers for deterministic ATS adapters
(Stagehand + Browserbase).

╔══════════════════════════════════════════════════════════════════════╗
║  LEGACY (Path B). Async helpers for the retired Stagehand adapters.  ║
║  The live pre-fill path uses the SYNC Playwright helpers in          ║
║  ``adapters/prepare_dom/_common.py`` — that is the only "_common"    ║
║  module under active maintenance. Do not extend this file.           ║
╚══════════════════════════════════════════════════════════════════════╝

Sibling: ``adapters/prepare_dom/_common.py`` holds
the sync Playwright DOM helpers used by the prepare-only fillers. Both files
exist because the two adapter tracks have incompatible runtimes — async
Stagehand ``act()`` / ``extract()`` vs sync ``page.locator().fill()`` — so
mixing the helpers in one module would obscure call signatures.

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
import time
from typing import Any

from adapters.base import FieldFill, FieldSkipped, SubmissionContext, SubmissionResult
from browser.session import sh_act, sh_extract

logger = logging.getLogger("submitter.adapter.common")


# ── Custom-question phase guards ─────────────────────────────────────────
#
# These are the abort knobs that keep a pathological form from burning the
# whole session budget inside the custom-question loop. They're loose enough
# to let a normal Anthropic/Stripe/Mercor form finish (Anthropic's Android
# SWE posting carries 18 custom questions; other enterprise boards run 15–20)
# but tight enough to catch runaway forms. The phase BUDGET is the actual
# runaway guard; the cap is defense-in-depth for an exotic or malicious form.
CUSTOM_Q_PHASE_BUDGET_SECONDS = 240   # 4 min wall-clock for the whole loop
CUSTOM_Q_MAX = 20                     # hard cap on questions we'll process


# ── Applicant profile access ─────────────────────────────────────────────

def applicant_fields(job: dict) -> dict[str, str]:
    """Pull applicant profile values off the job row.

    The M-1 schema persists this data in the ``form_answers`` JSONB column
    (populated by ``tailor.form_answers.generate_form_answers``). Older
    rows + tests may have used a nested ``applicant_profile`` blob or
    top-level keys instead, so the lookup falls back through all three
    in order: ``form_answers`` → ``applicant_profile`` → top-level row.

    The second bucket of keys (work_authorization onward) was added in #18
    to power the effectively-required custom-question answers surfaced by
    the 2026-04-21 Anthropic smoke run. These exist on every form as
    unasterisked but functionally mandatory fields (work auth, visa
    sponsorship, earliest start date, relocation / in-person willingness,
    AI usage policy, prior-interview history). Having truthful values
    here lets the three-tier classifier answer them instead of routing
    to review.
    """
    blob = job.get("form_answers") or job.get("applicant_profile") or {}
    if not isinstance(blob, dict):
        blob = {}

    def pick(*keys: str) -> str:
        for k in keys:
            v = blob.get(k) or job.get(k)
            if v:
                return str(v)
        return ""

    return {
        # ── Identity + contact ───────────────────────────────────────────
        # Each fallback chain ends with the M-1 form_answers key the tailor
        # actually writes (e.g. linkedin_url, portfolio_url, github_url),
        # but keeps older variants for back-compat with row schemas that
        # pre-date M-1.
        "first_name":   pick("first_name", "firstName", "candidate_first_name"),
        "last_name":    pick("last_name", "lastName", "candidate_last_name"),
        "full_name":    pick("full_name", "fullName", "candidate_full_name"),
        "email":        pick("email", "candidate_email"),
        "phone":        pick("phone", "phone_number", "candidate_phone"),
        "linkedin":     pick("linkedin_url", "linkedin", "candidate_linkedin"),
        "website":      pick("portfolio_url", "website", "personal_site"),
        "github":       pick("github_url", "github"),
        # form_answers stores location as ``current_location``; keep older
        # ``location`` and ``city`` variants for back-compat.
        "location":     pick("current_location", "location", "candidate_location", "city"),
        "current_company": pick("current_company", "candidate_company"),
        "current_title":   pick("current_title", "candidate_title", "job_title"),
        # ── Effectively-required form facts (#18) ────────────────────────
        # form_answers stores these under ``willing_to_relocate``,
        # ``remote_preference``, and ``availability_to_start``; M-1's
        # _build_identity_block also forwards ``visa_sponsorship_needed``,
        # ``ai_policy_ack``, and ``previous_interview_with_company`` from
        # profile.yml::application_defaults so the agent can answer those
        # required Anthropic-style questions.
        "work_authorization":      pick("work_authorization", "work_auth"),
        "visa_sponsorship_needed": pick("visa_sponsorship_needed", "visa_needed", "needs_sponsorship"),
        "earliest_start_date":     pick("availability_to_start", "earliest_start_date", "start_date", "availability"),
        "relocation_willingness":  pick("willing_to_relocate", "relocation_willingness", "relocation", "open_to_relocation"),
        "in_person_willingness":   pick("remote_preference", "in_person_willingness", "work_mode"),
        "ai_policy_ack":           pick("ai_policy_ack", "ai_usage_statement", "ai_disclosure"),
        "previous_interview_summary": _prior_interview_summary(blob, job),
    }


def _prior_interview_summary(profile: dict, job: dict) -> str:
    """Summarize `previous_interview_with_company` into a one-line statement.

    Profiles store this as a dict keyed by company slug with boolean values,
    e.g. `{"anthropic": False, "stripe": True}`. We flatten it into a single
    human-readable sentence the LLM can use when a form asks
    "Have you interviewed with <company> before?".
    """
    blob = (
        profile.get("previous_interview_with_company")
        or job.get("previous_interview_with_company")
        or {}
    )
    if not isinstance(blob, dict) or not blob:
        return ""
    yes = sorted(c for c, v in blob.items() if v)
    if not yes:
        return "no prior interviews with any listed company"
    return "previously interviewed at: " + ", ".join(yes)


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
#
# Three-tier policy (per Vishal, 2026-04-21):
#
#   required_by_form    : the form HTML marks the field required. Answer if
#                         we can; if not, route to review.
#   effectively_required: not marked required in the DOM, but a hiring
#                         manager would expect this answered (work auth,
#                         visa sponsorship, "why <company>", earliest
#                         start date, relocation / in-person willingness,
#                         AI usage policy acknowledgments, prior-interview
#                         history). Answer when we have a confident,
#                         truthful answer from profile/cover-letter; else
#                         skip without dropping confidence — we tried.
#   truly_optional      : demographic / preference data (pronunciation,
#                         pronouns, hobbies, "how did you hear", social
#                         URLs when resume is already attached, etc).
#                         Always skip.
#
# The LLM does the classification in the same call that produces the answer
# text. Truly_optional questions still cost one extract() to classify, but
# that's the minimum honest surface — without classification we can't tell
# "Why Anthropic?" from "How do you pronounce your name?".

CUSTOM_Q_ANSWER_SCHEMA: dict = {
    "type": "object",
    "properties": {
        "classification": {
            "type": "string",
            "enum": ["required_by_form", "effectively_required", "truly_optional"],
        },
        "decision": {
            "type": "string",
            "enum": ["answer", "skip"],
            "description": "must be 'skip' when classification == 'truly_optional'",
        },
        "answer": {"type": "string"},
        "reason": {"type": "string"},
    },
    "required": ["classification", "decision", "reason"],
}


_CUSTOM_Q_PROMPT_TEMPLATE = """\
Given this custom application question from a {ats_name} form:

Q: {label}
Type: {kind}. Marked required in form HTML: {required}.

Applicant context:
  Role applying for: {title}
  Cover letter (first 800 chars): {cover_letter_excerpt}

Applicant profile facts — use these verbatim to answer effectively-required
questions. Do NOT invent facts not present here.
  work_authorization:        {work_authorization}
  visa_sponsorship_needed:   {visa_sponsorship_needed}
  earliest_start_date:       {earliest_start_date}
  relocation_willingness:    {relocation_willingness}
  in_person_willingness:     {in_person_willingness}
  ai_policy_acknowledgement: {ai_policy_ack}
  prior_interviews:          {previous_interview_summary}

Step 1 — classify the question into exactly one of:

  • required_by_form: the form marks this field required (asterisk or the
    `required` attribute / aria-required).
  • effectively_required: not marked required in the DOM, but a hiring
    manager would expect it answered. Examples: US/country work
    authorization, visa sponsorship now/future, "Why <company>?",
    earliest start date, willingness to relocate, willingness to work
    in-person, AI usage policy acknowledgments, prior-interview history
    at this company, the address you plan to work from.
  • truly_optional: demographic or preference data that applicants commonly
    opt out of. Examples: name pronunciation, pronouns, preferred first
    name, hobbies, "how did you hear about us", general "additional
    information" prompts, social URLs (LinkedIn / GitHub / Publications /
    Website) when a resume is already attached, coding-language preference,
    "personal preferences", favorite-anything.

Step 2 — decide whether to fill:

  • classification == truly_optional → decision MUST be "skip".
  • classification in (effectively_required, required_by_form) → decision
    is "answer" if-and-only-if you have a CONFIDENT, TRUTHFUL answer
    derivable from the applicant context above. Otherwise "skip".
  • Never invent facts the applicant hasn't stated. If unsure → skip.

Return: classification, decision, answer (only if decision=="answer"),
and a brief reason.
"""


async def handle_custom_questions(
    sess: Any, page: Any, result: SubmissionResult,
    ctx: SubmissionContext, questions: list[dict], *, ats_name: str,
    phase_budget_s: float = CUSTOM_Q_PHASE_BUDGET_SECONDS,
    max_questions: int = CUSTOM_Q_MAX,
) -> None:
    """Drive the custom-question loop with phase-level abort guards.

    - Process at most `max_questions` questions (default 12). Extras land
      in skipped_fields with a "cap reached" reason.
    - Abort the loop if wall-clock in the phase exceeds `phase_budget_s`
      (default 240s). Remaining questions land with a "budget exceeded"
      reason and `recommend` drops to "needs_review".

    Individual per-call timeouts live one layer down in browser.session
    (SH_CALL_TIMEOUT_SECONDS). This loop guards the aggregate.
    """
    start = time.monotonic()
    for idx, q in enumerate(questions):
        if idx >= max_questions:
            _abort_remaining(result, questions[idx:], cause="cap", n_done=idx)
            return
        if time.monotonic() - start > phase_budget_s:
            _abort_remaining(result, questions[idx:], cause="phase budget", n_done=idx)
            return
        await handle_custom_question(sess, page, result, ctx, q, ats_name=ats_name)


def _abort_remaining(
    result: SubmissionResult, remaining: list[dict], *, cause: str, n_done: int,
) -> None:
    """Flush unreviewed custom questions as skips and force needs_review."""
    logger.warning(
        "custom-question phase aborted (%s) after %d questions; %d unreviewed",
        cause, n_done, len(remaining),
    )
    for q in remaining:
        result.skipped_fields.append(FieldSkipped(
            label=q.get("label", "?"),
            reason=f"custom-question phase aborted ({cause}); not reviewed",
        ))
    result.recommend = "needs_review"
    result.recommend_reason = (
        f"custom-question phase aborted: {cause} after {n_done} questions; "
        f"{len(remaining)} unreviewed"
    )
    # Leave confidence alone so score_and_recommend's core/required logic
    # can still add its own signal on top.


async def handle_custom_question(
    sess: Any, page: Any, result: SubmissionResult,
    ctx: SubmissionContext, q: dict, *, ats_name: str,
) -> None:
    """Classify one custom question and fill-or-skip per the three-tier policy.

    File uploads are never auto-answered regardless of classification.
    """
    label = q.get("label", "?")
    kind = q.get("kind", "text")
    required = bool(q.get("required"))

    if kind == "file":
        reason = (
            "required custom question (file upload)"
            if required else "custom question (file upload, policy: not auto-answered)"
        )
        result.skipped_fields.append(FieldSkipped(label=label, reason=reason))
        return

    app = applicant_fields(ctx.job)
    try:
        decision = await sh_extract(
            sess,
            instruction=_CUSTOM_Q_PROMPT_TEMPLATE.format(
                ats_name=ats_name,
                label=label,
                kind=kind,
                required=required,
                title=ctx.job.get("title", ""),
                cover_letter_excerpt=ctx.cover_letter_text[:800],
                work_authorization=app["work_authorization"] or "(not specified)",
                visa_sponsorship_needed=app["visa_sponsorship_needed"] or "(not specified)",
                earliest_start_date=app["earliest_start_date"] or "(not specified)",
                relocation_willingness=app["relocation_willingness"] or "(not specified)",
                in_person_willingness=app["in_person_willingness"] or "(not specified)",
                ai_policy_ack=app["ai_policy_ack"] or "(not specified)",
                previous_interview_summary=app["previous_interview_summary"] or "(not specified)",
            ),
            schema=CUSTOM_Q_ANSWER_SCHEMA,
            page=page,
        )
    except Exception as exc:
        logger.warning("classify '%s' failed: %s", label, exc)
        prefix = "required custom question" if required else "custom question"
        result.skipped_fields.append(FieldSkipped(
            label=label, reason=f"{prefix} (classify failed: {exc})",
        ))
        return

    if not isinstance(decision, dict):
        result.skipped_fields.append(FieldSkipped(
            label=label, reason="custom question (classify returned non-dict)",
        ))
        return

    classification = decision.get("classification") or "truly_optional"
    action = decision.get("decision")
    reason = decision.get("reason") or ""
    answer = (decision.get("answer") or "").strip()

    # Tier 3: truly optional — always skip, regardless of what the LLM said.
    if classification == "truly_optional":
        result.skipped_fields.append(FieldSkipped(
            label=label, reason=f"truly optional ({reason or 'policy'})",
        ))
        return

    # Tiers 1 & 2: either the form said required, or the LLM classified it
    # as effectively-required. Answer if we have a confident answer.
    is_form_required = required or classification == "required_by_form"
    prefix = "required custom question" if is_form_required else "effectively-required custom question"

    if action != "answer" or not answer:
        detail = reason or "no confident answer"
        result.skipped_fields.append(FieldSkipped(
            label=label, reason=f"{prefix} ({detail})",
        ))
        return

    try:
        await sh_act(sess, f"Answer the question '{label}' with: {answer}", page=page)
        result.filled_fields.append(FieldFill(
            label=label, value=answer, confidence=0.85, kind=kind or "text",
        ))
    except Exception as exc:
        result.skipped_fields.append(FieldSkipped(
            label=label, reason=f"{prefix} (act failed: {exc})",
        ))


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
