"""
review/packet.py — Build a review packet for jobs that land in needs_review.

A packet is the evidence a human needs to triage a failed-to-auto-submit job
in under a minute from the portfolio dashboard at /review/[job_id]. It
bundles: the pre-submit screenshot, the list of filled fields + their values,
the list of skipped/uncertain fields with reasons, and a deep link to the
Browserbase session replay.

The packet is stored as JSON in jobs.submission_log; any screenshots live in
Supabase Storage under job-materials/{job_id}/review/.

Stub: Milestone 5 fills in screenshot handling, storage uploads, and the
portfolio-facing shape. For now the function signatures document the
intended contract.
"""

from __future__ import annotations

import logging
from dataclasses import asdict
from typing import Any

from adapters.base import SubmissionResult
from config import REVIEW_DASHBOARD_URL

logger = logging.getLogger("submitter.review")


def build_packet(
    job: dict,
    result: SubmissionResult,
    attempt_n: int,
    stagehand_session_id: str | None,
    browserbase_replay_url: str | None,
    reason: str,
) -> dict[str, Any]:
    """
    Assemble the serializable packet the dashboard will render. Safe to call
    before any of the Milestone 5 UI work exists — nothing in this function
    touches the dashboard directly; it only produces the JSON structure.
    """
    return {
        "attempt_n": attempt_n,
        "adapter": result.adapter_name,
        "reason": reason,
        "confidence": result.confidence,
        "filled_fields": [asdict(f) for f in result.filled_fields],
        "skipped_fields": [asdict(f) for f in result.skipped_fields],
        "screenshots": [asdict(s) for s in result.screenshots],
        "stagehand_session_id": stagehand_session_id,
        "browserbase_replay_url": browserbase_replay_url,
        "agent_reasoning": result.agent_reasoning,
        "review_url": f"{REVIEW_DASHBOARD_URL}/{job['id']}",
    }
