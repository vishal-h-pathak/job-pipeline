"""
main.py — Polling loop for the job-submitter agent.

Reads jobs the tailor has marked ready, dispatches each to its ATS adapter
inside a fresh Browserbase session, runs confirm.py to decide auto-submit
vs needs_review, records the outcome.

Control flow (per job):

    1. db.get_jobs_ready_for_submission() — pull work
    2. For each job:
         a. Materials check (resume + CL present, materials_hash matches)
         b. db.mark_submitting() + db.open_attempt()
         c. browser.open_session(application_url)
         d. adapter = router.get_adapter(ats_kind); result = await adapter.run(ctx)
         e. db.record_submission_log(result)
         f. decision = confirm.decide(result, ats_kind)
               submit_and_verify -> confirm.click_submit_and_verify() -> mark_submitted | mark_failed
               route_to_review   -> build review packet, mark_needs_review
               abort             -> mark_failed
         g. db.close_attempt()

Stub — Milestone 2 gives us the skeleton, Milestone 3 wires it end-to-end
against Browserbase + Stagehand for Greenhouse. Deliberately a single-file
entry point so the control flow is easy to read.
"""

from __future__ import annotations

import asyncio
import logging
import signal
import sys
from pathlib import Path

import db
import router
import confirm
import storage
from adapters.base import SubmissionContext
from browser import session as browser_session
from config import (
    MAX_ATTEMPTS_PER_JOB,
    MAX_CONCURRENT_SUBMISSIONS,
    POLL_INTERVAL_SECONDS,
    SESSION_BUDGET_SECONDS,
)
from review.packet import build_packet

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(name)s %(levelname)s %(message)s",
)
logger = logging.getLogger("submitter.main")


# ── Per-job processing ───────────────────────────────────────────────────

async def process_one(job: dict) -> None:
    """Run a single submission attempt end-to-end. Never raises — all errors
    are translated into status transitions on the jobs row."""

    job_id = job["id"]
    ats_kind = job.get("ats_kind") or "generic"
    logger.info("processing job %s (ats=%s)", job_id, ats_kind)

    # Respect max attempts ceiling
    attempt_n = db.next_attempt_n(job_id)
    if attempt_n > MAX_ATTEMPTS_PER_JOB:
        db.mark_failed(job_id, f"exceeded max attempts ({MAX_ATTEMPTS_PER_JOB})")
        return

    # Materials hydration
    try:
        resume_local = storage.download_to_tmp(job["resume_pdf_path"], suffix=".pdf")
        cover_local = storage.download_to_tmp(job["cover_letter_pdf_path"], suffix=".pdf")
        cover_text = job.get("cover_letter_path") or ""
        if not db.verify_materials_hash(job, resume_local.read_bytes(), cover_text):
            db.mark_needs_review(job_id, reason="materials_hash mismatch")
            return
    except Exception as exc:
        logger.exception("materials hydration failed for %s", job_id)
        db.mark_failed(job_id, f"materials hydration: {exc}")
        return

    db.mark_submitting(job_id)
    adapter = router.get_adapter(ats_kind)
    attempt_id = db.open_attempt(job_id, attempt_n, adapter.name)

    try:
        async with browser_session.open_session(job["application_url"]) as handle:
            ctx = SubmissionContext(
                job=job,
                resume_pdf_path=Path(resume_local),
                cover_letter_pdf_path=Path(cover_local),
                cover_letter_text=cover_text,
                application_url=job["application_url"],
                page=handle.page,
                attempt_n=attempt_n,
            )
            result = await asyncio.wait_for(
                adapter.run(ctx),
                timeout=SESSION_BUDGET_SECONDS,
            )
            result.adapter_name = adapter.name
            db.record_submission_log(
                job_id,
                log={
                    "attempt_n": attempt_n,
                    "adapter": adapter.name,
                    "filled_fields": [f.__dict__ for f in result.filled_fields],
                    "skipped_fields": [s.__dict__ for s in result.skipped_fields],
                    "screenshots": [s.__dict__ for s in result.screenshots],
                    "stagehand_session_id": handle.stagehand_session_id,
                    "browserbase_replay_url": handle.browserbase_replay_url,
                    "agent_reasoning": result.agent_reasoning,
                    "error": result.error,
                },
                confidence=result.confidence,
            )

            decision = confirm.decide(result, ats_kind)
            if decision == "submit_and_verify":
                outcome = await confirm.click_submit_and_verify(ctx, result)
                if outcome.decision == "submit_and_verify":
                    db.mark_submitted(job_id, outcome.evidence)
                    db.close_attempt(
                        attempt_id, outcome="submitted",
                        confidence=result.confidence,
                        stagehand_session_id=handle.stagehand_session_id,
                        browserbase_replay_url=handle.browserbase_replay_url,
                        notes={"evidence": outcome.evidence},
                    )
                    return
                # verification said no — fall through to review
                decision = "route_to_review"

            if decision == "route_to_review":
                packet = build_packet(
                    job, result, attempt_n,
                    handle.stagehand_session_id,
                    handle.browserbase_replay_url,
                    reason=result.recommend_reason or "confidence below threshold",
                )
                db.mark_needs_review(job_id, reason=packet["reason"])
                db.close_attempt(
                    attempt_id, outcome="needs_review",
                    confidence=result.confidence,
                    stagehand_session_id=handle.stagehand_session_id,
                    browserbase_replay_url=handle.browserbase_replay_url,
                    notes=packet,
                )
                return

            # abort
            db.mark_failed(job_id, reason=result.error or "adapter aborted")
            db.close_attempt(
                attempt_id, outcome="failed",
                confidence=result.confidence,
                stagehand_session_id=handle.stagehand_session_id,
                browserbase_replay_url=handle.browserbase_replay_url,
                notes={"error": result.error},
            )

    except asyncio.TimeoutError:
        db.mark_needs_review(job_id, reason=f"session budget ({SESSION_BUDGET_SECONDS}s) exceeded")
        db.close_attempt(attempt_id, outcome="needs_review", notes={"error": "timeout"})
    except NotImplementedError as exc:
        # Scaffold-phase guard; clearer than a silent traceback.
        logger.error("scaffold stub hit: %s", exc)
        db.close_attempt(attempt_id, outcome="failed", notes={"error": str(exc)})
        raise
    except Exception as exc:
        logger.exception("unexpected failure on job %s", job_id)
        db.mark_failed(job_id, reason=f"{type(exc).__name__}: {exc}")
        db.close_attempt(attempt_id, outcome="failed", notes={"error": str(exc)})
    finally:
        for p in (resume_local, cover_local):
            try:
                p.unlink(missing_ok=True)
            except Exception:
                pass


# ── Poll loop ────────────────────────────────────────────────────────────

_stop = asyncio.Event()


def _install_signal_handlers() -> None:
    def handler(signum, _frame):
        logger.info("received signal %s, shutting down", signum)
        _stop.set()
    signal.signal(signal.SIGINT, handler)
    signal.signal(signal.SIGTERM, handler)


async def main_loop() -> None:
    logger.info("submitter starting — poll every %ds, max %d concurrent",
                POLL_INTERVAL_SECONDS, MAX_CONCURRENT_SUBMISSIONS)
    sem = asyncio.Semaphore(MAX_CONCURRENT_SUBMISSIONS)
    while not _stop.is_set():
        jobs = db.get_jobs_ready_for_submission(limit=MAX_CONCURRENT_SUBMISSIONS * 4)
        if not jobs:
            logger.debug("no ready jobs")
        tasks = []
        for job in jobs:
            async def _bounded(j=job):
                async with sem:
                    await process_one(j)
            tasks.append(asyncio.create_task(_bounded()))
        if tasks:
            await asyncio.gather(*tasks, return_exceptions=True)
        try:
            await asyncio.wait_for(_stop.wait(), timeout=POLL_INTERVAL_SECONDS)
        except asyncio.TimeoutError:
            pass
    logger.info("submitter stopped")


if __name__ == "__main__":
    _install_signal_handlers()
    try:
        asyncio.run(main_loop())
    except KeyboardInterrupt:
        pass
    except Exception:
        logger.exception("fatal")
        sys.exit(1)
