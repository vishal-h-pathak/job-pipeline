"""
confirm.py — Decide whether to click submit, then verify it landed.

The only module that applies the auto-submit-vs-needs-review policy. Adapters
hand us a SubmissionResult; we read its confidence + recommend fields against
AUTO_SUBMIT_THRESHOLD (with per-ATS overrides in config.ATS_CONFIDENCE_MIN),
and either:

    1. Click submit, then verify success with an ATS-appropriate signal
       (URL redirect, DOM marker, or — fallback — a bounded Claude call
       against the post-submit screenshot).
    2. Route the job to needs_review without clicking.
    3. Abort (fatal) and mark the job failed.

Verification signals are layered by strength: URL-based > DOM-marker >
LLM-judge. Adapter-specific detectors short-circuit the ladder when they
fire; the LLM fallback is the "unknown ATS" safety net.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Any, Literal

from adapters.base import SubmissionContext, SubmissionResult
from browser.session import sh_act, sh_extract
from jobpipe.config import ATS_CONFIDENCE_MIN, AUTO_SUBMIT_THRESHOLD

logger = logging.getLogger("submitter.confirm")

Decision = Literal["submit_and_verify", "route_to_review", "abort"]


@dataclass
class ConfirmationOutcome:
    """What click_submit_and_verify reports back to main.process_one."""
    decision: Decision                                   # "submit_and_verify" on success, else "route_to_review" / "abort"
    evidence: dict[str, Any] = field(default_factory=dict)  # {"kind": "url_redirect", "detail": "..."}
    reason: str = ""
    confidence_effective: float = 0.0


def decide(result: SubmissionResult, ats_kind: str) -> Decision:
    """Apply the policy without side effects — pure decision."""
    if result.error or result.recommend == "abort":
        return "abort"

    threshold = ATS_CONFIDENCE_MIN.get(ats_kind, AUTO_SUBMIT_THRESHOLD)
    if result.recommend == "needs_review":
        return "route_to_review"
    if result.confidence < threshold:
        logger.info(
            "confidence %.2f < threshold %.2f for ats=%s — routing to review",
            result.confidence, threshold, ats_kind,
        )
        return "route_to_review"
    return "submit_and_verify"


# ── Per-ATS success signals ──────────────────────────────────────────────
#
# Add here as new deterministic adapters land. Each entry is (url_needle,
# page_text_needle). Either needle matching is enough to treat the submit
# as confirmed; both missing drops us to the LLM judge.

_SUCCESS_SIGNALS: dict[str, tuple[tuple[str, ...], tuple[str, ...]]] = {
    "greenhouse": (
        ("/applications/thank_you", "thank-you", "thanks-for-applying"),
        ("Thanks for applying", "Application submitted", "We’ve received your application",
         "We've received your application"),
    ),
    "lever": (
        ("/thanks", "/thank-you", "application-submitted"),
        ("Thanks for your application", "Application received"),
    ),
    "ashby": (
        ("/thanks", "application-submitted", "thank_you"),
        ("Thanks for applying", "Your application has been submitted",
         "Application received"),
    ),
}


# ── Submit button click ──────────────────────────────────────────────────

async def _click_submit(ctx: SubmissionContext) -> None:
    """Click the primary submit button. Adapter-agnostic: Greenhouse, Lever,
    and Ashby all label it something close to 'Submit Application'."""
    await sh_act(
        ctx.stagehand_session,
        "Click the primary form submission button — usually labelled "
        "'Submit Application', 'Submit', or 'Apply'. Do NOT click any "
        "'Save', 'Review', or 'Back' buttons.",
        page=ctx.page,
    )
    # Brief settle — let redirect / XHR complete before we probe for evidence.
    try:
        await ctx.page.wait_for_load_state("networkidle", timeout=15_000)
    except Exception:
        logger.debug("networkidle wait timed out; continuing to signal check")


# ── Signal probes ────────────────────────────────────────────────────────

async def _probe_url_and_text(ctx: SubmissionContext, ats_kind: str) -> tuple[str | None, str]:
    """Return (match_kind, detail) if a deterministic signal fires, else (None, '').
    match_kind is 'url_redirect' or 'page_text'."""
    url_needles, text_needles = _SUCCESS_SIGNALS.get(ats_kind, ((), ()))

    try:
        current_url = ctx.page.url
    except Exception:
        current_url = ""
    for needle in url_needles:
        if needle and needle in current_url:
            return "url_redirect", current_url

    if text_needles:
        try:
            text = (await ctx.page.content())[:50_000]  # cap; we only need the banner area
        except Exception:
            text = ""
        for needle in text_needles:
            if needle and needle in text:
                return "page_text", needle

    return None, ""


_LLM_JUDGE_SCHEMA: dict = {
    "type": "object",
    "properties": {
        "submitted": {"type": "boolean"},
        "evidence":  {"type": "string", "description": "quoted text or selector seen on page"},
        "confidence": {"type": "number", "description": "0..1"},
    },
    "required": ["submitted", "confidence"],
}


async def _llm_judge(ctx: SubmissionContext) -> dict[str, Any]:
    """Last-resort judge: let Stagehand's extract read the DOM and decide."""
    result = await sh_extract(
        ctx.stagehand_session,
        instruction=(
            "Look at the current page and decide whether a job application "
            "was just successfully submitted. Acceptable evidence: a "
            "confirmation banner, a URL redirect to a thank-you page, a "
            "message containing 'Thanks for applying' / 'Application "
            "received' / similar. Return submitted=true with quoted "
            "evidence text if so. Return submitted=false with confidence "
            "high if you see the form is still present with validation "
            "errors, and low if the state is ambiguous."
        ),
        schema=_LLM_JUDGE_SCHEMA,
        page=ctx.page,
    )
    return result if isinstance(result, dict) else {"submitted": False, "confidence": 0.0}


# ── Public entry point ───────────────────────────────────────────────────

async def click_submit_and_verify(
    ctx: SubmissionContext,
    result: SubmissionResult,
) -> ConfirmationOutcome:
    """Click submit, then verify via the strongest available signal."""
    ats_kind = ctx.job.get("ats_kind") or result.adapter_name or "generic"

    try:
        await _click_submit(ctx)
    except Exception as exc:
        logger.exception("submit click failed")
        return ConfirmationOutcome(
            decision="route_to_review",
            evidence={"kind": "click_failed", "detail": str(exc)},
            reason="could not click submit button",
            confidence_effective=result.confidence,
        )

    # 1. Deterministic signal.
    kind, detail = await _probe_url_and_text(ctx, ats_kind)
    if kind:
        return ConfirmationOutcome(
            decision="submit_and_verify",
            evidence={"kind": kind, "detail": detail, "ats": ats_kind},
            reason=f"{ats_kind}: {kind} match",
            confidence_effective=result.confidence,
        )

    # 2. LLM fallback — cheap, bounded by Stagehand's extract budget.
    try:
        judge = await _llm_judge(ctx)
    except Exception as exc:
        logger.warning("LLM judge failed: %s", exc)
        judge = {"submitted": False, "confidence": 0.0, "evidence": str(exc)}

    if judge.get("submitted") and float(judge.get("confidence", 0.0)) >= 0.80:
        return ConfirmationOutcome(
            decision="submit_and_verify",
            evidence={"kind": "llm_judge", "detail": judge.get("evidence", ""),
                      "ats": ats_kind, "confidence": judge.get("confidence")},
            reason=f"{ats_kind}: LLM judge confirmed submission",
            confidence_effective=result.confidence,
        )

    # 3. No signal fired — route to review so a human can inspect the replay.
    return ConfirmationOutcome(
        decision="route_to_review",
        evidence={"kind": "no_signal", "judge": judge, "ats": ats_kind},
        reason="no deterministic success signal and LLM judge uncertain",
        confidence_effective=result.confidence,
    )
