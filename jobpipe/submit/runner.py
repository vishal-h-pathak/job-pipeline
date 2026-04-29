"""
runner.py — Polling loop for the job-submitter agent.

Reads jobs the cockpit has queued for pre-fill (status='prefilling'),
dispatches each to its ATS adapter inside a fresh Browserbase session,
records the outcome, and stops short of clicking Submit. Under the M-2
career-ops alignment, the human always clicks Submit themselves; the
submitter's job is to fill the form, take a screenshot, and stand down.

Control flow (per job):

    1. db.get_jobs_ready_for_submission() — pull 'prefilling' jobs
    2. For each job:
         a. Materials check (resume + CL present, materials_hash matches)
         b. db.mark_submitting() + db.open_attempt()
            (mark_submitting is now a no-op for status — it stays
            'prefilling' — the function name is kept for back-compat.)
         c. browser.open_session(application_url)
         d. adapter = router.get_adapter(ats_kind); result = await adapter.run(ctx)
         e. db.record_submission_log(result)
         f. Take a "form_filled" screenshot, upload to Storage.
         g. Branch on adapter result:
               error / recommend=abort -> db.mark_failed
               otherwise               -> db.mark_awaiting_submit
                  (the cockpit shows the screenshot + log + recommend reason;
                  the human reviews, fixes anything wrong, clicks Submit)
         h. db.close_attempt()

Note: previous versions of this loop called confirm.click_submit_and_verify
to auto-click Submit when adapter confidence cleared a threshold. That path
is intentionally removed under M-2 — no automated path ever clicks Submit.
confirm.py is left in the repo for now (its decide() helper + ATS success
signals are still useful reference material for the cockpit's Mark Applied
flow) but runner.py no longer imports it.

Wired as ``jobpipe-submit = jobpipe.submit.runner:run`` in pyproject.toml
(see :func:`run` at the bottom of the file).
"""

from __future__ import annotations

# ── sys.path bootstrap ────────────────────────────────────────────────────
# The submit subtree's intra-subtree modules use unprefixed imports
# (``import router``, ``import confirm``, ``import storage``,
# ``from adapters.base import X``, ``from browser.session import Y``,
# ``from review_packet import build_packet``). When this module is
# imported as ``jobpipe.submit.runner`` (e.g. via the ``jobpipe-submit``
# console script), sys.path won't contain ``jobpipe/submit/`` and those
# bare imports would fail. Insert the directory before any other imports
# run so every downstream module load resolves cleanly. PR-9 rewrote the
# cross-cutting bare imports (``import db``, ``from config import ...``)
# to canonical ``jobpipe.*`` paths (with the fail-loud secrets coming
# from ``jobpipe.submit.config``) and deleted the per-subtree db.py
# shim; the bootstrap stays for the intra-subtree imports above.
import sys as _sys
from pathlib import Path as _Path

_SUBMIT_DIR = str(_Path(__file__).resolve().parent)
if _SUBMIT_DIR not in _sys.path:
    _sys.path.insert(0, _SUBMIT_DIR)
del _sys, _Path, _SUBMIT_DIR
# ──────────────────────────────────────────────────────────────────────────

import asyncio  # noqa: E402
import logging  # noqa: E402
import signal  # noqa: E402
import sys  # noqa: E402
from pathlib import Path  # noqa: E402

import jobpipe.db as db  # noqa: E402
import router  # noqa: E402
import storage  # noqa: E402
from adapters.base import SubmissionContext  # noqa: E402
from browser import session as browser_session  # noqa: E402
from jobpipe.config import (  # noqa: E402
    MAX_ATTEMPTS_PER_JOB,
    MAX_CONCURRENT_SUBMISSIONS,
    POLL_INTERVAL_SECONDS,
    SESSION_BUDGET_SECONDS,
)
# confirm.py and review_packet.py belong to the legacy auto-submit-and-
# verify flow. M-2 removed that path: the runner pre-fills only and the
# cockpit renders the structured submission_log + form_filled screenshot
# directly. Both modules are kept in the repo as reference for the
# cockpit's Mark Applied verification work, but runner.py no longer
# imports them.

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
                stagehand_session=handle.stagehand_session,
                page=handle.page,
                attempt_n=attempt_n,
            )
            result = await asyncio.wait_for(
                adapter.run(ctx),
                timeout=SESSION_BUDGET_SECONDS,
            )
            result.adapter_name = adapter.name

            # M-2: take a "form_filled" screenshot the cockpit can render
            # inline before any further state changes. Best-effort — a
            # screenshot failure is not worth aborting the attempt.
            screenshot_storage_path: str | None = None
            try:
                png = await handle.page.screenshot(full_page=True)
                screenshot_storage_path = storage.upload_review_screenshot(
                    job_id, label="form_filled", png_bytes=png,
                )
            except Exception:
                logger.exception("post-fill screenshot upload failed")

            db.record_submission_log(
                job_id,
                log={
                    "attempt_n": attempt_n,
                    "adapter": adapter.name,
                    "filled_fields": [f.__dict__ for f in result.filled_fields],
                    "skipped_fields": [s.__dict__ for s in result.skipped_fields],
                    "screenshots": [s.__dict__ for s in result.screenshots],
                    "form_filled_screenshot_path": screenshot_storage_path,
                    "stagehand_session_id": handle.stagehand_session_id,
                    "browserbase_replay_url": handle.browserbase_replay_url,
                    "agent_reasoning": result.agent_reasoning,
                    "recommend": result.recommend,
                    "recommend_reason": result.recommend_reason,
                    "error": result.error,
                },
                confidence=result.confidence,
            )

            # Branch on adapter outcome. Under M-2 there are exactly two
            # post-fill terminal states for the submitter: failed (couldn't
            # complete pre-fill) and awaiting_human_submit (pre-fill done,
            # human takes over). The recommend/confidence fields surface
            # to the cockpit but never trigger an auto-click.
            if result.error or result.recommend == "abort":
                db.mark_failed(job_id, reason=result.error or "adapter aborted")
                db.close_attempt(
                    attempt_id, outcome="failed",
                    confidence=result.confidence,
                    stagehand_session_id=handle.stagehand_session_id,
                    browserbase_replay_url=handle.browserbase_replay_url,
                    notes={"error": result.error,
                           "recommend": result.recommend,
                           "recommend_reason": result.recommend_reason},
                )
                return

            db.mark_awaiting_submit(
                job_id, screenshot_path=screenshot_storage_path,
            )
            # application_attempts.outcome enum is independent of jobs.status
            # and still uses {submitted, needs_review, failed, in_progress}.
            # We use 'submitted' here to mean "submitter completed its
            # pre-fill work cleanly" and 'needs_review' to mean "completed
            # but flagged uncertainty". Neither implies the application was
            # actually submitted to the company — that only happens when
            # the human clicks Submit + Mark Applied in the cockpit.
            audit_outcome = "submitted" if result.recommend == "auto_submit" else "needs_review"
            db.close_attempt(
                attempt_id, outcome=audit_outcome,
                confidence=result.confidence,
                stagehand_session_id=handle.stagehand_session_id,
                browserbase_replay_url=handle.browserbase_replay_url,
                notes={
                    "recommend": result.recommend,
                    "recommend_reason": result.recommend_reason,
                    "form_filled_screenshot_path": screenshot_storage_path,
                },
            )

    except asyncio.TimeoutError:
        db.mark_failed(job_id, reason=f"session budget ({SESSION_BUDGET_SECONDS}s) exceeded")
        db.close_attempt(attempt_id, outcome="failed", notes={"error": "timeout"})
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


def run() -> None:
    """Console-script entry point for ``jobpipe-submit``.

    Installs SIGINT/SIGTERM handlers, then drives :func:`main_loop` under
    ``asyncio.run``. Wired as ``jobpipe-submit = jobpipe.submit.runner:run``
    in pyproject.toml. The legacy ``python runner.py`` invocation falls
    through here too via the ``__main__`` guard below.
    """
    _install_signal_handlers()
    try:
        asyncio.run(main_loop())
    except KeyboardInterrupt:
        pass
    except Exception:
        logger.exception("fatal")
        sys.exit(1)


if __name__ == "__main__":
    run()
