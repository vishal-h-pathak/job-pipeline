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

Stub module: policy scaffold in place, per-ATS success signals land in
Milestone 3 alongside the Greenhouse adapter.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Any, Literal

from adapters.base import SubmissionContext, SubmissionResult
from config import ATS_CONFIDENCE_MIN, AUTO_SUBMIT_THRESHOLD

logger = logging.getLogger("submitter.confirm")

Decision = Literal["submit_and_verify", "route_to_review", "abort"]


@dataclass
class ConfirmationOutcome:
    decision: Decision
    evidence: dict[str, Any]              # kind, detail (e.g. {"kind": "url_redirect", "detail": "/applications/thank_you"})
    reason: str                           # human-readable
    confidence_effective: float           # threshold actually applied


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


async def click_submit_and_verify(
    ctx: SubmissionContext,
    result: SubmissionResult,
) -> ConfirmationOutcome:
    """
    Click the submit button, then verify using an ATS-appropriate signal.

    NOTE: Milestone 3 stub. Raises NotImplementedError so accidental runs
    fail loudly. The Greenhouse-E2E work fills this in with:
      - the per-ATS _verify_submit_<kind>() dispatcher
      - a fallback LLM-backed verification against a post-submit screenshot
    """
    _ = (ctx, result)
    raise NotImplementedError(
        "confirm.click_submit_and_verify is a scaffold stub. "
        "Wire up per-ATS success-signal detection in Milestone 3."
    )
