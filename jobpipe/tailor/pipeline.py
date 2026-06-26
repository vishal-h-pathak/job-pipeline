"""
pipeline.py — Tailor + Submit Agent entry points (renamed from main.py
in PR-4; split into two console scripts in PR-13).

Polls Supabase for approved / prefill-requested jobs and runs the
matching phase. PR-13 split the previous combined cycle into two
narrower entry points so an automated trigger (CI, cron) can run
tailoring — pure LLM + LaTeX + Storage, headless-friendly — without
dragging in the visible-browser pre-fill phase that needs a human at
the keyboard.

Wiring (pyproject.toml::[project.scripts]):
    jobpipe-tailor = jobpipe.tailor.pipeline:run_tailor_only
    jobpipe-submit = jobpipe.tailor.pipeline:run_submit_only

The combined ``run()`` / ``run_cycle()`` pair is kept for backward
compatibility with tools that invoke them directly (e.g. the legacy
smoke harness); ``run_cycle()`` emits a deprecation log line on every
invocation pointing callers at the split entry points.

Usage:
    jobpipe-tailor                          # Tailor one cycle (approved jobs)
    jobpipe-tailor --status                 # Print job counts by status
    jobpipe-tailor --test-tailor <job_id>   # Test material generation for a job (no status changes)
    jobpipe-submit                          # Pre-fill one cycle (prefill_requested jobs)
    jobpipe-submit --status                 # Same status output as the tailor

NOTE: ``jobpipe-submit`` here is the local-Playwright pre-fill phase.
The retired Browserbase + Stagehand runner lives at
``jobpipe/submit/runner_legacy.py`` and has no console-script binding.
"""

from __future__ import annotations

# ── sys.path bootstrap ────────────────────────────────────────────────────
# The tailor subtree's intra-subtree modules use unprefixed imports
# (``from storage import ...``, ``from tailor.X import ...``,
# ``from prompts import ...``, ``from interview_prep.X import ...``). When this module is imported
# as ``jobpipe.tailor.pipeline`` (e.g. via the ``jobpipe-tailor`` console
# script), sys.path won't contain ``jobpipe/tailor/`` and those bare
# imports would fail. Insert the directory before any other imports run
# so every downstream module load resolves cleanly. PR-9 rewrote the
# cross-cutting bare imports (``import db``, ``import config``,
# ``from notify import ...``) to canonical ``jobpipe.*`` paths and
# deleted the per-subtree shims they resolved through; the bootstrap
# stays for the intra-subtree imports above.
import sys as _sys
from pathlib import Path as _Path

_TAILOR_DIR = str(_Path(__file__).resolve().parent)
if _TAILOR_DIR not in _sys.path:
    _sys.path.insert(0, _TAILOR_DIR)
del _sys, _Path, _TAILOR_DIR
# ──────────────────────────────────────────────────────────────────────────

import argparse  # noqa: E402
import logging  # noqa: E402
import sys  # noqa: E402
import time  # noqa: E402
from datetime import datetime  # noqa: E402
from pathlib import Path  # noqa: E402

from jobpipe.config import (  # noqa: E402
    POLL_INTERVAL_MINUTES,
    HUMAN_APPROVAL_REQUIRED,
    MAX_ATTEMPTS_PER_JOB,
    SUBMIT_POLL_INTERVAL_SECONDS,
    SUPABASE_URL,
    SUPABASE_SERVICE_ROLE_KEY,
    WATCH_POLL_INTERVAL_SECONDS,
    WATCH_RECONNECT_MIN_SECONDS,
    WATCH_RECONNECT_MAX_SECONDS,
    JOBPIPE_WATCHER_ID,
)
from jobpipe.db import (  # noqa: E402
    get_approved_jobs,
    get_active_watcher_id,
    get_job,
    get_prefill_requested_jobs,
    get_watcher_heartbeats,
    record_heartbeat,
    set_active_watcher_id,
    mark_preparing,
    mark_ready_for_review,
    mark_awaiting_submit,
    mark_applied,
    mark_tailor_failed,
    mark_skipped,
    get_job_counts_by_status,
    next_attempt_n,
    open_attempt,
    close_attempt,
    record_prefill_verification,
)
from tailor.resume import tailor_resume  # noqa: E402
from tailor.cover_letter import generate_cover_letter  # noqa: E402
from tailor.cover_letter_pdf import render_cover_letter_pdf  # noqa: E402
from tailor.latex_resume import generate_tailored_latex  # noqa: E402
from tailor.form_answers import generate_form_answers  # noqa: E402
from jobpipe.shared.ats_detect import detect_ats, get_applicant  # noqa: E402
from jobpipe.shared import cost  # noqa: E402  (cost-event attribution context)
from interview_prep.generator import generate_stories  # noqa: E402
from interview_prep.bank import save_stories  # noqa: E402
from jobpipe.notify import (  # noqa: E402  PR-8: canonical send_* names
    send_awaiting_review,
    send_awaiting_submit,
    send_failed,
)
from jobpipe.shared.storage import download_to_tmp  # noqa: E402
from jobpipe.submit.handoff import assisted_manual_handoff  # noqa: E402
from jobpipe.submit.verify import build_prefill_verification  # noqa: E402
from storage import (  # noqa: E402
    upload_pdf,
    upload_prefill_screenshot,
)

# ── Logging ──────────────────────────────────────────────────────────────────
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s — %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
logger = logging.getLogger("pipeline")


# ── Cost guards ──────────────────────────────────────────────────────────────
# Bulk-cron path only — per-row dashboard clicks bypass this. Rows scored
# below SCORE_THRESHOLD don't get tailored when process_approved_jobs runs
# every approved row in a loop, since we won't notify or apply to them
# anyway. When the human clicks Tailor on a specific card, that's an
# explicit "do the full pipeline" signal and process_one_approved_job
# runs unconditionally.
SCORE_THRESHOLD: int = 6


def process_one_approved_job(job_id: str) -> None:
    """Public per-row tailor entry: open a cost-attribution context, delegate.

    Wraps the whole per-row pipeline in
    ``cost_context(stage="tailor", job_id=job_id)`` (S2) so every Anthropic
    call the tailor modules make through ``llm.complete()`` lands a
    ``cost_events`` row attributed to this job. No tailor-side runs-row id
    exists, so ``run_id`` stays ``None`` (the rows are still job/stage-useful,
    just not run-rolled-up). All three callers — the bulk loop, the
    ``--job-id`` CLI, and the manual handoff CLI — route through here, so the
    context is set in exactly one place. Work lives in
    :func:`_process_one_approved_job`.
    """
    with cost.cost_context(stage="tailor", job_id=job_id):
        _process_one_approved_job(job_id)


def _process_one_approved_job(job_id: str) -> None:
    """Tailor a single ``approved`` row end-to-end (PR-14).

    Fetches the row by id, validates ``status == 'approved'``, then runs
    the same per-row tailoring pipeline that ``process_approved_jobs``
    runs inside its loop. Two callers:

      - ``process_approved_jobs()`` — the loop body delegates here so
        the bulk path is the same code as the per-row path.
      - ``run_tailor_only --job-id <uuid>`` — dashboard "Tailor" button
        and CLI per-row invocation.

    Returns silently (with a log line) when the row is missing or no
    longer ``approved`` — guards against a stale dashboard click
    re-tailoring a row another process already moved on.
    """
    job = get_job(job_id)
    if job is None:
        logger.warning(
            f"process_one_approved_job: job not found id={job_id}"
        )
        return
    if job.get("status") != "approved":
        logger.info(
            f"process_one_approved_job: skipping {job_id} "
            f"(status={job.get('status')!r}, not 'approved')"
        )
        return

    company = job.get("company", "Unknown")
    title = job.get("title", "Unknown")
    url = job.get("url", "")

    logger.info(f"Processing: {company} — {title}")

    # ── Check ATS type ───────────────────────────────────────────────
    ats = detect_ats(url)
    if ats == "linkedin":
        logger.info(f"LinkedIn detected — flagging for manual application")
        mark_ready_for_review(
            job_id,
            application_notes="LinkedIn: human-only application required",
        )
        send_awaiting_review(job)
        return

    # ── Mark as preparing ────────────────────────────────────────────
    mark_preparing(job_id)

    try:
        # ── Hydrate the persisted Match Agent chat (if any) into the
        # job dict so the tailor prompts see Vishal's own framing for
        # this specific role. The dashboard's MatchAgent.tsx writes
        # the conversation array to jobs.match_chat after each turn;
        # here we render it to plain text and store it under the key
        # the tailor functions expect.
        chat = job.get("match_chat") or []
        if chat:
            transcript_lines = []
            for msg in chat:
                role = (msg.get("role") or "").upper()
                content = (msg.get("content") or "").strip()
                if not content:
                    continue
                transcript_lines.append(f"{role}: {content}")
            job["match_chat_transcript"] = "\n\n".join(transcript_lines)
            logger.info(
                f"Match Agent chat injected for {company} "
                f"({len(chat)} turns, {len(job['match_chat_transcript'])} chars)"
            )

        # ── Tailor resume (returns metadata only — no disk writes) ───
        logger.info(f"Tailoring resume for {company}...")
        resume_result = tailor_resume(job)

        # ── Generate cover letter text ───────────────────────────────
        logger.info(f"Generating cover letter for {company}...")
        cover_result = generate_cover_letter(job, resume_result)
        cover_text = cover_result.get("cover_letter", "")

        # ── Render LaTeX resume PDF (in-memory) ──────────────────────
        logger.info(f"Generating tailored LaTeX resume PDF for {company}...")
        latex_result = generate_tailored_latex(job, resume_result)
        resume_pdf_bytes = latex_result.get("pdf_bytes")
        if not latex_result.get("compile_success") or not resume_pdf_bytes:
            raise RuntimeError(
                f"Resume PDF compile failed: "
                f"{latex_result.get('compile_log', '(no log)')[:300]}"
            )

        # ── Render cover letter PDF (in-memory) ─────────────────────
        cover_pdf_bytes = render_cover_letter_pdf(
            cover_text, company=company, role=title,
        )

        # ── Upload both PDFs to Supabase Storage ────────────────────
        logger.info(f"Uploading PDFs to Storage for job {job_id}...")
        resume_storage_path = upload_pdf(job_id, "resume", resume_pdf_bytes)
        cover_storage_path = upload_pdf(job_id, "cover_letter", cover_pdf_bytes)

        # ── Resolve apply URL (cheap — no agent loop) ───────────────
        # If the job URL is an aggregator (Remotive, CareerVault, etc.),
        # find the real ATS apply link via httpx + BeautifulSoup.
        # No Anthropic calls, no browser.
        #
        # The expensive form-fill agent loop has moved to
        # process_confirmed_jobs — it now only runs after the human
        # clicks "Confirm Submit" in the dashboard. This keeps the
        # tailoring phase fast and cheap so materials can be generated
        # and reviewed without committing to a submission attempt.
        from url_resolver import resolve_application_url
        logger.info(f"Resolving apply URL for {company}...")
        resolved = resolve_application_url(url)
        resolved_url = resolved.get("resolved") or url
        resolver_notes = resolved.get("notes", "no resolution needed")

        resolved_ats = detect_ats(resolved_url)
        applicant = get_applicant(resolved_url)
        application_notes = (
            f"ATS: {resolved_ats}\n"
            f"Original URL: {url}\n"
            f"Resolved URL: {resolved_url}\n"
            f"Resolver: {resolver_notes}\n"
            f"Auto-submittable: "
            f"{'yes' if applicant else 'no — manual form fill needed'}"
        )

        # ── Build resume tailoring summary for dashboard ─────────────
        # Keep it in the resume_path column (TEXT containing JSON) so the
        # existing ReviewPanel parser works unchanged. The pdf_path key
        # now references the Supabase Storage object, not a local file.
        import json
        resume_summary = json.dumps({
            "tailored_summary": resume_result.get("tailored_summary", ""),
            "emphasis_areas": resume_result.get("emphasis_areas", []),
            "keywords_to_include": resume_result.get("keywords_to_include", []),
            "experience_order": resume_result.get("experience_order", []),
            "suggested_bullets": resume_result.get("suggested_bullets", {}),
            "skills_section": resume_result.get("skills_section", {}),
            "diff_notes": resume_result.get("diff_notes", ""),
            "storage_path": resume_storage_path,
            "compile_success": True,
        })

        # ── Pull archetype off the resume_result for analytics ──────
        # tailor_resume() stamps `_archetype` on its return dict (J-4).
        # Persist the key + confidence so /dashboard/insights and the
        # pattern-analysis script can group by lane.
        archetype_meta = resume_result.get("_archetype") or {}

        # ── Generate STAR+R interview stories (J-3) ─────────────────
        # Side effect of tailoring — accumulate stories into the
        # star_stories table so before any interview Vishal can pull
        # 5-10 master stories tagged to the role's archetype + skills.
        # Failures are non-fatal: tailoring + submission still proceed.
        try:
            stories = generate_stories(job, archetype_meta=archetype_meta)
            if stories:
                save_stories(
                    job_id=job_id,
                    archetype=archetype_meta.get("archetype"),
                    company=company,
                    role=title,
                    stories=stories,
                )
                logger.info(f"Generated {len(stories)} STAR+R stories for {company}")
        except Exception as exc:
            logger.warning(f"STAR+R generation skipped for {company}: {exc}")

        # ── Generate form-answer drafts (M-1, career-ops "Block H") ──
        # Authoritative source for the per-ATS DOM handlers (M-3) and
        # the dashboard cockpit (M-6). Identity / contact / location /
        # comp / work-auth fields come from profile.yml in Python; the
        # LLM only drafts why_this_role, why_this_company, optional
        # additional_info, and any role-specific additional_questions.
        #
        # Unconditional here — clicking the dashboard "Tailor" button
        # implies the user wants the full pipeline regardless of score,
        # and the bulk-cron cost guard now lives in
        # process_approved_jobs() (which skips low-score rows before
        # delegating). Generation failures stay non-fatal so a flaky
        # LLM call doesn't fail the whole tailor.
        try:
            form_answers = generate_form_answers(
                job, resume_result, archetype_meta=archetype_meta
            )
            from jobpipe.db import client as _db_client
            _db_client.table("jobs").update(
                {"form_answers": form_answers}
            ).eq("id", job_id).execute()
            logger.info(
                f"Form answers generated for {company} "
                f"({len(form_answers.get('additional_questions') or [])} "
                f"role-specific Qs)"
            )
        except Exception as exc:
            logger.warning(
                f"form_answers generation skipped for {company}: {exc}"
            )

        # ── Mark ready for review ────────────────────────────────────
        # Save the RESOLVED url so process_confirmed_jobs points the
        # submission agent at the real ATS page, not the aggregator.
        mark_ready_for_review(
            job_id,
            resume_path=resume_summary,
            cover_letter_path=cover_text,
            application_url=resolved_url,
            application_notes=application_notes,
            resume_pdf_path=resume_storage_path,
            cover_letter_pdf_path=cover_storage_path,
            archetype=archetype_meta.get("archetype"),
            archetype_confidence=archetype_meta.get("confidence"),
        )

        send_awaiting_review(job)
        logger.info(f"Ready for review: {company} — {title}")

    except Exception as e:
        logger.error(f"Failed to process {company} — {title}: {e}")
        # mark_tailor_failed clears materials by default; the prior
        # explicit delete_all_for_job is now redundant and removed.
        mark_tailor_failed(job_id, str(e))
        send_failed(job, str(e))


def process_approved_jobs():
    """
    Phase 1: Pick up approved jobs, tailor materials, fill forms,
    then pause at ready_to_submit for human review.

    PR-14: the per-row body lives in :func:`process_one_approved_job`.
    This function loops every ``approved`` row and delegates so the
    bulk path (cron / CLI / no-arg dashboard click) and the per-row
    path (dashboard "Tailor" button on a single card) share the same
    code.

    Cost guard: skips rows whose score is below
    :data:`SCORE_THRESHOLD` so the bulk-cron path doesn't burn an
    LLM call on a row we won't notify or apply to. Per-row dashboard
    clicks bypass this entirely by calling
    :func:`process_one_approved_job` directly — clicking Tailor
    implies the user wants the full pipeline regardless of score.

    Score-type drift: some Supabase rows store ``score`` as a string
    (e.g. ``"7"``), others as int. ``"7" >= 6`` raises ``TypeError``
    in Py3, so we coerce locally; investigate upstream writer
    separately.
    """
    jobs = get_approved_jobs()
    if not jobs:
        return

    logger.info(f"Found {len(jobs)} approved job(s) to process")

    for job in jobs:
        job_id = job["id"]
        try:
            score = int(job.get("score") or 0)
        except (TypeError, ValueError):
            score = 0
        if score < SCORE_THRESHOLD:
            company = job.get("company", "Unknown")
            logger.info(
                f"Skipping {company} ({job_id}) — score {score} "
                f"< SCORE_THRESHOLD={SCORE_THRESHOLD} "
                f"(bulk-cron cost guard; per-row clicks bypass)"
            )
            continue
        process_one_approved_job(job_id)


def process_prefill_requested_jobs():
    """
    Phase 2 (M-5 / local-browser rewrite): Pick up jobs the user clicked
    "Pre-fill Form" on, open ONE visible, logged-in browser for the whole
    run, and process the queue by opening a new tab (page) per job in that
    same window. Each job dispatches to the per-ATS handler (Ashby /
    Greenhouse / Lever) or the prepare-only vision agent, captures the
    post-fill screenshot, marks the row `awaiting_human_submit`, and BLOCKS
    on terminal input() so the user can review what was typed, fix anything
    wrong, and click Submit themselves.

    Strictly serial — one form at a time. The human can only review one
    form at a time.

    Two behaviours added by the local-browser rewrite:

      - **Persistent, logged-in browser.** The browser is created once via
        ``open_browser_context`` — a persistent Chrome profile by default
        (``JOBPIPE_BROWSER_PROFILE``) so ATS logins persist across runs, a
        CDP attach when ``JOBPIPE_BROWSER_CDP`` is set, or a cookieless
        headless launch under ``HEADLESS`` / no display (tests + CI).
      - **Always-assisted-manual fallback.** EVERY non-success prepare exit
        (agent ``queue_for_review``, an adapter exception, a non-success
        result, a page that fails to load) degrades to
        :func:`assisted_manual_handoff` — tab left open, materials staged
        locally, a checklist written to the row, status
        ``awaiting_human_submit`` — instead of a bare failure. Only
        *pre-browser* preconditions (max attempts, no resume PDF, resume
        download failure) still hard-fail, because there is no open tab to
        hand off.
    """
    # Lazy imports so the module stays importable without Playwright
    # installed (e.g. for --status / --test-tailor).
    from playwright.sync_api import sync_playwright
    from jobpipe.submit.browser.local import open_browser_context, is_headless

    jobs = get_prefill_requested_jobs()
    if not jobs:
        return

    logger.info(f"Found {len(jobs)} prefill-requested job(s)")

    dispatch = _make_prefill_dispatch()

    # One browser window for the whole run; a new tab per job (Part 1). The
    # context owns the window — close it once, after the last job. Individual
    # tabs are intentionally NOT closed per job here: closing on
    # success→next belongs to the later verification-loop work.
    with sync_playwright() as pw:
        context, close_browser = open_browser_context(pw, headless=is_headless())
        try:
            for job in jobs:
                dispatch(job, context)
        finally:
            close_browser()


def _make_prefill_dispatch():
    """Build a ``dispatch(job, context)`` callable wired with the lazy-imported
    prepare-flow deps.

    Both the one-shot ``process_prefill_requested_jobs`` cycle and the
    long-lived ``run_submit_watch`` watcher fill a job the same way — open a tab
    in the shared context, run the per-ATS handler (or the universal vision
    fallback), then block on the stop-and-wait advance. Centralising the import
    wiring here keeps the two call sites from drifting.
    """
    from jobpipe.shared.ats_detect import detect_ats, get_applicant
    from jobpipe.submit.adapters.prepare_dom.universal import UniversalApplicant
    from url_resolver import resolve_application_url
    import json

    def _dispatch(job, context):
        _prefill_one_job(
            job,
            context,
            detect_ats=detect_ats,
            get_applicant=get_applicant,
            UniversalApplicant=UniversalApplicant,
            resolve_application_url=resolve_application_url,
            json=json,
        )

    return _dispatch


def _prefill_one_job(job, context, *, detect_ats, get_applicant,
                     UniversalApplicant, resolve_application_url, json):
    """Pre-fill one job in a new tab of the shared, logged-in browser.

    Pre-browser preconditions hard-fail (no tab to hand off). Once the tab
    is open, every non-success exit degrades to an assisted-manual hand-off.
    """
    job_id = job["id"]
    company = job.get("company", "Unknown")
    title = job.get("title", "Unknown")
    url = (
        job.get("submission_url")
        or job.get("application_url")
        or job.get("url", "")
    )

    logger.info(f"Pre-filling: {company} — {title}  ({url})")

    # ── Pre-browser preconditions (hard-fail: no open tab to hand off) ──
    # Max-attempts ceiling — mirrors the runner.py check so the local path
    # enforces the same per-job retry budget. Pre-attempt-row exit.
    attempt_n = next_attempt_n(job_id)
    if attempt_n > MAX_ATTEMPTS_PER_JOB:
        mark_tailor_failed(
            job_id,
            f"exceeded max attempts ({MAX_ATTEMPTS_PER_JOB})",
            clear_materials=False,
        )
        send_failed(job, f"max attempts ({MAX_ATTEMPTS_PER_JOB}) exceeded")
        return

    # Resolve aggregator → real ATS once up front (no LLM call).
    try:
        resolved = resolve_application_url(url)
        real_url = resolved.get("resolved") or url
    except Exception as exc:
        logger.warning(f"URL resolve failed for {company}: {exc}")
        real_url = url

    applicant = get_applicant(real_url)
    ats = detect_ats(real_url)

    # Pull resume PDF to a tmp file (ATS uploads need a real path).
    tmp_resume_pdf = None
    storage_path = job.get("resume_pdf_path")
    if not storage_path:
        raw_resume = job.get("resume_path") or ""
        try:
            meta = json.loads(raw_resume) if raw_resume else {}
            storage_path = meta.get("storage_path") or meta.get("pdf_path")
        except Exception:
            pass

    if not storage_path:
        mark_tailor_failed(
            job_id,
            "Pre-fill: no resume PDF in storage; re-tailor first.",
            clear_materials=False,
        )
        send_failed(job, "Pre-fill blocked: no resume PDF.")
        return

    try:
        tmp_resume_pdf = download_to_tmp(storage_path)
    except Exception as exc:
        mark_tailor_failed(
            job_id,
            f"Pre-fill: resume download failed: {exc}",
            clear_materials=False,
        )
        send_failed(job, f"Pre-fill blocked: {exc}")
        return

    cover_letter_text = job.get("cover_letter_path") or ""

    # Open the audit row AFTER materials hydration but BEFORE the tab opens —
    # matches runner.py's ordering so the two paths write equivalent
    # application_attempts trails. ``adapter`` is the picked applicant's
    # ``name`` attribute (e.g. ``"greenhouse"``).
    attempt_id = open_attempt(job_id, attempt_n, adapter=applicant.name)
    attempt_closed = False

    # New tab in the shared window. Left open across the input() block and
    # NOT closed on the way out (success or hand-off) — the window is torn
    # down once at end-of-run.
    page = context.new_page()

    def _handoff(reason, unfilled=None, screenshot_key=None, summary=None):
        """Degrade to assisted-manual: tab open, files staged, checklist."""
        nonlocal attempt_closed
        ho = assisted_manual_handoff(
            page, job, reason, unfilled=unfilled or [], summary=summary,
        )
        if not attempt_closed:
            close_attempt(
                attempt_id,
                outcome="needs_review",
                notes={
                    "assisted_manual": True,
                    "reason": reason,
                    "materials_dir": ho.get("materials_dir"),
                    "prefill_screenshot_path": screenshot_key,
                    "verification": summary,
                },
            )
            attempt_closed = True
        # awaiting_human_submit notification — "human, take over in the tab".
        send_awaiting_submit(job, screenshot_key)
        return ho

    try:
        # ── Navigate ────────────────────────────────────────────────────
        try:
            page.goto(real_url, wait_until="domcontentloaded", timeout=45000)
            try:
                page.wait_for_load_state("networkidle", timeout=10000)
            except Exception:
                pass
        except Exception as exc:
            # Tab is open on the (failed) URL — hand off rather than fail.
            _handoff(f"page failed to load: {exc}")
            _wait_for_human_decision(page=page, job_id=job_id)
            return

        # Per-ATS handlers expose fill_form(page, job, ...).
        # UniversalApplicant exposes apply_with_page (M-5 helper).
        if isinstance(applicant, UniversalApplicant):
            result = applicant.apply_with_page(
                page, job,
                resume_path=str(tmp_resume_pdf),
                cover_letter_path=cover_letter_text,
            )
        else:
            result = applicant.fill_form(
                page, job,
                resume_path=str(tmp_resume_pdf),
                cover_letter_path=cover_letter_text,
            )

        # Final post-fill screenshot for the cockpit.
        screenshot_storage_key = None
        try:
            png_bytes = page.screenshot(full_page=False)
            screenshot_storage_key = upload_prefill_screenshot(job_id, png_bytes)
        except Exception as exc:
            logger.warning(f"Could not upload prefill screenshot: {exc}")

        # ── Verification pass (Part B) ───────────────────────────────────
        # Runs for BOTH the clean-success and the assisted-manual paths so
        # the cockpit always shows "filled X of Y; still needs: ...". The
        # already-captured post-fill screenshot doubles as the review image
        # (no second capture). Structured count -> jobs.submission_log;
        # human summary -> application_notes (success) or the hand-off notes.
        verification = build_prefill_verification(result, ats)
        record_prefill_verification(
            job_id, {**verification, "screenshot": screenshot_storage_key},
        )
        logger.info("Verification for %s: %s", company, verification["summary"])

        bar = "=" * 60
        if result.get("success"):
            mark_awaiting_submit(
                job_id,
                screenshot_path=screenshot_storage_key,
                application_notes=verification["summary"],
            )
            # Close the audit row BEFORE the wait so the dashboard sees
            # ``outcome`` immediately. ``outcome="submitted"`` means the
            # adapter pre-filled cleanly — NOT that the app was submitted;
            # ``applied`` only flips when the human clicks Mark Applied.
            close_attempt(
                attempt_id,
                outcome="submitted",
                notes={
                    "prefill_screenshot_path": screenshot_storage_key,
                    "filled_fields": result.get("fields_filled"),
                    "notes": result.get("notes"),
                    "verification": verification["summary"],
                },
            )
            attempt_closed = True
            send_awaiting_submit(job, screenshot_storage_key)
            print(
                f"\n{bar}\n"
                f"  Form pre-filled for {company} - {title}\n"
                f"  ATS: {ats}  ({type(applicant).__name__})\n"
                f"  {verification['summary']}\n"
                f"  Browser is open. Review what was typed, click "
                f"Submit yourself,\n"
                f"  then click 'Submitted ✓ → Next' (or 'Skip') in the "
                f"dashboard.\n"
                f"  This loop advances automatically when you do.\n"
                f"{bar}"
            )
        else:
            # Non-success result (e.g. agent queue_for_review, or a fill that
            # left required fields empty) → assisted-manual hand-off.
            reason = (
                result.get("review_reason")
                or result.get("notes")
                or "pre-fill did not complete cleanly"
            )
            _handoff(
                reason,
                unfilled=verification["still_needs"],
                screenshot_key=screenshot_storage_key,
                summary=verification["summary"],
            )

        _wait_for_human_decision(page=page, job_id=job_id)

    except Exception as exc:
        # Adapter / dispatch exception — the tab is open, so hand off rather
        # than emit a bare failure.
        logger.exception(f"Pre-fill exception for {company}: {exc}")
        try:
            _handoff(f"adapter exception: {exc}")
        except Exception:
            logger.exception("assisted_manual_handoff failed for %s", job_id)
            if not attempt_closed:
                try:
                    close_attempt(
                        attempt_id, outcome="failed",
                        notes={"error": str(exc)},
                    )
                except Exception:
                    logger.exception("close_attempt failed for %s", job_id)
        _wait_for_human_decision(page=page, job_id=job_id)
    finally:
        if tmp_resume_pdf is not None:
            try:
                Path(tmp_resume_pdf).unlink(missing_ok=True)
            except Exception:
                pass


# Terminal decisions the human can reach from the dashboard while the tab is
# open. ``applied`` is the cockpit's "Submitted ✓ → Next" (reusing the
# existing mark-applied route); ``skipped`` is the "Skip" button. ``failed`` /
# ``expired`` are defensive so an out-of-band flip never strands the loop.
_DECISION_STATUSES = frozenset({"applied", "skipped", "failed", "expired"})


def _wait_for_human_decision(page, job_id: str, *,
                             poll_interval: int = None, sleep=None) -> str | None:
    """Stop-and-wait advance (Part B): keep the pre-filled tab OPEN and poll
    the jobs row until the human flips it to a terminal decision in the
    dashboard, then close the tab so the loop moves to the next job.

    Replaces the old ``input()``-gated advance, which forced the human to come
    back to the terminal and guessed nothing about whether they'd actually
    submitted. Now the dashboard button ("Submitted ✓ → Next" → ``applied``,
    or "Skip" → ``skipped``) is the single advance signal.

    Returns the terminal status, or ``None`` if interrupted (Ctrl-C / EOF).
    The tab is closed in all cases so the shared window doesn't accumulate
    orphaned tabs. ``poll_interval`` / ``sleep`` are injectable for tests.
    """
    interval = (
        poll_interval if poll_interval is not None
        else SUBMIT_POLL_INTERVAL_SECONDS
    )
    _sleep = sleep or time.sleep
    decision = None
    try:
        while True:
            job = get_job(job_id) or {}
            status = job.get("status")
            if status in _DECISION_STATUSES:
                decision = status
                break
            _sleep(interval)
    except (EOFError, KeyboardInterrupt):
        decision = None
    finally:
        try:
            page.close()
        except Exception:
            pass
    if decision:
        logger.info("Job %s human decision: %s — advancing", job_id, decision)
    else:
        logger.info("Job %s wait interrupted — advancing", job_id)
    return decision


def run_cycle():
    """Run one complete poll cycle (M-7).

    DEPRECATED (PR-13): use ``run_tailor_only()`` for the tailoring
    phase and ``run_submit_only()`` for the visible-browser pre-fill
    phase. The combined cycle survives only for tools that already
    invoke it directly (e.g. ``scripts/smoke_legacy.py``).

    Two phases per cycle, both strictly serial:
      1. process_approved_jobs() — tailoring + form_answers generation
         for every job the user approved. Lands rows in ready_for_review.
      2. process_prefill_requested_jobs() — for every row the user
         clicked "Pre-fill Form" on in the cockpit, opens a visible
         browser, dispatches to the per-ATS DOM handler (Ashby /
         Greenhouse / Lever) or the prepare-only vision agent, takes a
         screenshot, marks awaiting_human_submit, then BLOCKS on
         input() so the human can review and submit themselves.

    The system never auto-clicks Submit. The cockpit's "Mark Applied"
    button is the single source of truth for whether a row was actually
    submitted (M-6).
    """
    logger.warning(
        "run_cycle() is deprecated (PR-13); use run_tailor_only() or "
        "run_submit_only() instead. Continuing with combined cycle for "
        "backward compatibility."
    )
    logger.info(f"=== Cycle at {datetime.utcnow().isoformat()} ===")
    process_approved_jobs()
    process_prefill_requested_jobs()


def print_status():
    """Print current job counts by status."""
    counts = get_job_counts_by_status()
    print("\nJob Status Summary:")
    print("-" * 35)
    for status, count in sorted(counts.items()):
        print(f"  {status:20s} {count:>5d}")
    print(f"  {'TOTAL':20s} {sum(counts.values()):>5d}")
    print()


def test_tailor(job_id: str):
    """
    Test material generation for a single job without changing its status.

    Fetches the job from Supabase, runs resume tailoring + cover letter + LaTeX,
    and prints everything to stdout for review.
    """
    from jobpipe.db import client as supabase_client
    import json

    print(f"\n{'='*60}")
    print(f"  TEST TAILOR — job_id: {job_id}")
    print(f"{'='*60}\n")

    # Fetch job
    result = supabase_client.table("jobs").select("*").eq("id", job_id).execute()
    if not result.data:
        print(f"ERROR: No job found with id '{job_id}'")
        print("Hint: use --status to see available jobs, or check the id in Supabase.")
        return

    job = result.data[0]
    print(f"Job: {job.get('title')} at {job.get('company')}")
    print(f"Tier: {job.get('tier')} | Score: {job.get('score')} | Status: {job.get('status')}")
    print(f"URL: {job.get('url')}")
    ats = detect_ats(job.get("url", ""))
    print(f"ATS: {ats}")
    print()

    # Step 1: Tailor resume
    print("─── STEP 1: Resume Tailoring ───────────────────────────────")
    resume_result = tailor_resume(job)
    print(f"\nSummary:\n  {resume_result.get('tailored_summary', 'N/A')}\n")
    print(f"Emphasis areas: {', '.join(resume_result.get('emphasis_areas', []))}")
    print(f"Keywords: {', '.join(resume_result.get('keywords_to_include', []))}")
    print(f"Experience order: {', '.join(resume_result.get('experience_order', []))}")
    print(f"\nDiff notes: {resume_result.get('diff_notes', 'N/A')}")

    # Step 2: Cover letter
    print("\n─── STEP 2: Cover Letter ───────────────────────────────────")
    cover_result = generate_cover_letter(job, resume_result)
    print(f"\n{cover_result.get('cover_letter', 'N/A')}")

    # Step 3: LaTeX resume (in-memory)
    print("\n─── STEP 3: LaTeX Resume PDF ───────────────────────────────")
    latex_result = generate_tailored_latex(job, resume_result)
    if latex_result.get("compile_success"):
        print(f"PDF compiled successfully ({len(latex_result.get('pdf_bytes') or b'')} bytes, in-memory)")
    else:
        print(f"LaTeX compilation failed: {latex_result.get('compile_log', 'unknown')[:500]}")

    # Step 4: STAR+R interview stories (J-3)
    # Mirrors the production flow at process_approved_jobs(): generates
    # stories tagged to the archetype, but in test mode we print them
    # to stdout instead of saving to the star_stories table.
    print("\n─── STEP 4: STAR+R Interview Stories ───────────────────────")
    archetype_meta = resume_result.get("_archetype") or {}
    try:
        stories = generate_stories(job, archetype_meta=archetype_meta)
        if not stories:
            print("(generator returned no stories)")
        else:
            print(f"\nGenerated {len(stories)} stories"
                  f" (archetype: {archetype_meta.get('archetype', 'unknown')}):\n")
            for i, s in enumerate(stories, 1):
                print(f"  ── Story {i} ──")
                for field in ("situation", "task", "action", "result", "reflection"):
                    val = s.get(field)
                    if val:
                        print(f"  {field.upper():11s} {val}")
                tags = s.get("tags") or []
                if tags:
                    print(f"  TAGS        {', '.join(tags)}")
                print()
    except Exception as exc:
        print(f"(STAR+R generation failed: {exc})")

    # Step 5: Form-answer drafts (M-1, career-ops "Block H")
    # Always runs in test mode regardless of score so the user can preview
    # what gets stored in jobs.form_answers and copy-pasted into manual
    # submissions. The production flow at process_approved_jobs() gates
    # this on score >= 6.
    print("\n─── STEP 5: Form-Answer Drafts ─────────────────────────────")
    try:
        form_answers = generate_form_answers(
            job, resume_result, archetype_meta=archetype_meta
        )
        identity_keys = (
            "first_name", "last_name", "email", "phone", "linkedin_url",
            "github_url", "portfolio_url", "current_location",
            "willing_to_relocate", "remote_preference", "salary_expectation",
            "work_authorization", "notice_period", "availability_to_start",
            "current_company", "current_title", "years_of_experience",
        )
        print("\nIDENTITY (from profile.yml — never LLM-generated):")
        for k in identity_keys:
            v = form_answers.get(k)
            if v is None or v == "":
                continue
            print(f"  {k:24s} {v}")

        print("\nWHY THIS ROLE:")
        print(f"  {form_answers.get('why_this_role') or '(empty)'}")

        print("\nWHY THIS COMPANY:")
        print(f"  {form_answers.get('why_this_company') or '(empty)'}")

        print("\nADDITIONAL INFO:")
        print(f"  {form_answers.get('additional_info') or '(none)'}")

        questions = form_answers.get("additional_questions") or []
        print(f"\nADDITIONAL QUESTIONS ({len(questions)}):")
        if not questions:
            print("  (none)")
        for i, q in enumerate(questions, 1):
            print(f"\n  Q{i}: {q.get('question', '')}")
            print(f"  A{i}: {q.get('draft_answer', '')}")
    except Exception as exc:
        print(f"(form_answers generation failed: {exc})")

    # Summary
    print(f"\n{'='*60}")
    print("  DONE — Review the outputs above.")
    print("  (Test mode writes nothing to disk; run `jobpipe-tailor` for")
    print("   the real pipeline which uploads to Supabase Storage.)")
    print(f"{'='*60}\n")


def run() -> None:
    """Console-script entry point wired as ``jobpipe-tailor`` in pyproject.toml.

    Parses CLI args (``--once``, ``--status``, ``--test-tailor``) and
    runs one cycle. The polling loop has been removed to prevent
    unattended API usage; schedule via cron / Cowork if you want
    repeated runs.
    """
    parser = argparse.ArgumentParser(description="Job Tailor Agent")
    parser.add_argument("--once", action="store_true", help="Run one cycle and exit")
    parser.add_argument("--status", action="store_true", help="Print job counts by status")
    parser.add_argument("--test-tailor", metavar="JOB_ID",
                        help="Test material generation for a job (no status changes)")
    args = parser.parse_args()

    if args.status:
        print_status()
        return

    if args.test_tailor:
        test_tailor(args.test_tailor)
        return

    print(f"""
╔═══════════════════════════════════════════════╗
║       JOB TAILOR AGENT                        ║
║  {datetime.utcnow().strftime('%Y-%m-%d %H:%M:%S')} UTC{' ' * 21}║
║  Poll interval: {POLL_INTERVAL_MINUTES} min{' ' * 24}║
║  Human approval: {'ON' if HUMAN_APPROVAL_REQUIRED else 'OFF'}{' ' * 24}║
╚═══════════════════════════════════════════════╝
""")

    # Always run a single cycle. Use --once for clarity, but bare invocation
    # also runs once. The polling loop has been removed to prevent
    # unattended API usage. To run on a schedule, use an external scheduler
    # (cron, Cowork scheduled task, etc.)
    run_cycle()


def run_tailor_only() -> None:
    """Console-script entry point for ``jobpipe-tailor`` (PR-13 split).

    Runs ``process_approved_jobs()`` only — resume + cover letter +
    LaTeX render + form_answers generation + STAR+R interview stories.
    Pure LLM + LaTeX + Supabase Storage; no browser. Suitable for
    GitHub Actions workflow_dispatch (Phase 3) and any other automated
    trigger that should NOT open a visible browser.

    The visible-browser pre-fill phase used to share this entry point
    via ``run_cycle()``; it now lives behind ``run_submit_only`` /
    ``jobpipe-submit``.

    Args mirror the prior ``run()`` shape: ``--once`` (default
    behavior — bare invocation also runs once), ``--status`` (job
    counts), ``--test-tailor`` (single-job dry run with no status
    changes).
    """
    parser = argparse.ArgumentParser(
        description="Job Tailor Agent — tailoring only (PR-13 split)"
    )
    parser.add_argument(
        "--once", action="store_true", help="Run one cycle and exit"
    )
    parser.add_argument(
        "--status", action="store_true", help="Print job counts by status"
    )
    parser.add_argument(
        "--test-tailor", metavar="JOB_ID",
        help="Test material generation for a job (no status changes)",
    )
    parser.add_argument(
        "--job-id", metavar="JOB_ID", default=None,
        help="Tailor a single approved row by id (PR-14 per-row tailor). "
             "When omitted, tailors every row with status='approved'.",
    )
    args = parser.parse_args()

    if args.status:
        print_status()
        return

    if args.test_tailor:
        test_tailor(args.test_tailor)
        return

    logger.info(f"=== Tailor cycle at {datetime.utcnow().isoformat()} ===")
    if args.job_id:
        process_one_approved_job(args.job_id)
    else:
        process_approved_jobs()


def run_submit_only() -> None:
    """Console-script entry point for ``jobpipe-submit`` (PR-13 split).

    Runs ``process_prefill_requested_jobs()`` only — opens a visible
    browser per row that was clicked "Pre-fill Form" in the cockpit,
    dispatches to the per-ATS DOM handler (Ashby / Greenhouse / Lever)
    or the prepare-only vision agent, takes a post-fill screenshot,
    marks ``awaiting_human_submit``, and BLOCKS on terminal ``input()``
    so the human can review the visible browser, click Submit
    themselves, and come back to the dashboard cockpit to click "Mark
    Applied".

    NOT the retired Browserbase + Stagehand path — that lives at
    ``jobpipe/submit/runner_legacy.py`` and has no console-script
    binding. PR-13 reused the ``jobpipe-submit`` script name on
    purpose.

    Minimal arg surface (no ``--test-tailor`` — that's tailor-side
    only): ``--once`` runs one cycle and exits; ``--status`` prints job
    counts.
    """
    parser = argparse.ArgumentParser(
        description="Job Submit Agent — pre-fill only (PR-13 split)"
    )
    parser.add_argument(
        "--once", action="store_true",
        help="Run one pre-fill cycle and exit",
    )
    parser.add_argument(
        "--status", action="store_true", help="Print job counts by status"
    )
    parser.add_argument(
        "--watch", action="store_true",
        help="Stay alive and drive the browser on each dashboard 'Pre-fill' "
             "click via Supabase Realtime (idle on a websocket otherwise).",
    )
    parser.add_argument(
        "--poll", type=int, nargs="?", const=0, default=None, metavar="SECONDS",
        help="With --watch, use the polling fallback instead of Realtime: "
             "re-scan the prefilling queue every SECONDS "
             f"(default {WATCH_POLL_INTERVAL_SECONDS}s when no value given).",
    )
    # Dual-machine toggle (feat/dual-machine-watcher). Flippable from any
    # terminal on either machine; the dashboard writes the same row.
    parser.add_argument(
        "--set-active", nargs="?", const="", default=None, metavar="WATCHER_ID",
        help="Set which machine may claim jobs (e.g. 'macbook' / 'desktop'). "
             "With no value, print the current active watcher instead.",
    )
    parser.add_argument(
        "--who-is-active", action="store_true",
        help="Print the active watcher id and the known machine heartbeats.",
    )
    args = parser.parse_args()

    # Coordination CLI is a pure DB op — no browser, handled before anything else.
    if args.who_is_active or args.set_active == "":
        _print_active_watcher()
        return
    if args.set_active is not None:
        set_active_watcher_id(args.set_active)
        print(f"active watcher set to {args.set_active!r}")
        return

    if args.status:
        print_status()
        return

    if args.watch or args.poll is not None:
        run_submit_watch(poll_seconds=args.poll)
        return

    logger.info(f"=== Submit (pre-fill) cycle at {datetime.utcnow().isoformat()} ===")
    process_prefill_requested_jobs()


def _print_active_watcher() -> None:
    """Print the active watcher id + known machine heartbeats (``--who-is-active``).

    Read-only coordination view, shared by ``--who-is-active`` and a bare
    ``--set-active`` (no value). Marks this machine and flags stale heartbeats so
    the user can tell at a glance which machine is live and which is set active.
    """
    active = get_active_watcher_id()
    print(f"active_watcher_id = {active or '(none set)'}")
    print(f"this machine      = {JOBPIPE_WATCHER_ID}")
    heartbeats = get_watcher_heartbeats()
    if not heartbeats:
        print("no heartbeats recorded yet (no watcher has run since migration 015)")
        return
    print("\nmachines:")
    for hb in heartbeats:
        wid = hb.get("watcher_id", "?")
        marks = []
        if wid == active:
            marks.append("ACTIVE")
        if wid == JOBPIPE_WATCHER_ID:
            marks.append("this")
        suffix = f"  [{', '.join(marks)}]" if marks else ""
        print(
            f"  - {wid:<12} state={hb.get('state', '?'):<8} "
            f"last_seen={hb.get('last_seen', '?')}{suffix}"
        )


def build_submit_watcher(*, poll_seconds: int | None = None):
    """Construct a :class:`SubmitWatcher` wired to the real prepare flow.

    Factored out of :func:`run_submit_watch` so the wiring (which collaborators
    the watcher gets) is unit-testable without opening a browser or a websocket.
    ``poll_seconds`` selects the ``--poll`` fallback transport; ``None`` uses
    Supabase Realtime.
    """
    from playwright.sync_api import sync_playwright
    from jobpipe.submit.browser.local import open_browser_context, is_headless
    from jobpipe.submit.watch import (
        SubmitWatcher,
        PollEventSource,
        RealtimeEventSource,
        WatcherCoordinator,
    )

    dispatch = _make_prefill_dispatch()

    # Dual-machine gate: this machine only claims jobs when it is the active
    # watcher. The coordinator writes a heartbeat each cycle either way so the
    # dashboard can show liveness. (feat/dual-machine-watcher)
    coordinator = WatcherCoordinator(
        watcher_id=JOBPIPE_WATCHER_ID,
        get_active=get_active_watcher_id,
        record_heartbeat=record_heartbeat,
    )

    def _open_context():
        # The watcher owns one persistent context for its whole lifetime. We
        # keep the sync_playwright() context-manager open by stashing it on the
        # closer so teardown stops Playwright too.
        pw_cm = sync_playwright()
        pw = pw_cm.__enter__()
        context, close_browser = open_browser_context(pw, headless=is_headless())

        def _closer():
            try:
                close_browser()
            finally:
                pw_cm.__exit__(None, None, None)

        return context, _closer

    def _make_source(enqueue, stop_event):
        if poll_seconds is not None:
            return PollEventSource(
                enqueue, stop_event,
                interval=poll_seconds or WATCH_POLL_INTERVAL_SECONDS,
            )
        return RealtimeEventSource(
            enqueue, stop_event,
            url=SUPABASE_URL,
            token=SUPABASE_SERVICE_ROLE_KEY,
            reconnect_min=WATCH_RECONNECT_MIN_SECONDS,
            reconnect_max=WATCH_RECONNECT_MAX_SECONDS,
        )

    return SubmitWatcher(
        open_context=_open_context,
        fetch_pending=get_prefill_requested_jobs,
        fetch_one=get_job,
        process_one=dispatch,
        make_source=_make_source,
        coordinate=coordinator.should_claim,
    )


def run_submit_watch(*, poll_seconds: int | None = None) -> None:
    """Long-lived local watcher: hold one browser open, act on dashboard clicks.

    Default transport is Supabase Realtime (idle on a websocket, acts the instant
    a row hits ``status='prefilling'``). ``poll_seconds`` switches to the
    ``--poll`` fallback. Runs until SIGINT; on Ctrl-C it closes the browser
    cleanly. See :mod:`jobpipe.submit.watch` for the threading model and
    ``jobpipe/submit/README.md`` for setup (one-time ATS login, ``channel=chrome``,
    launchd auto-start).
    """
    import signal

    transport = "poll" if poll_seconds is not None else "realtime"
    logger.info(
        "=== Submit watcher starting (%s, id=%s) at %s ===",
        transport, JOBPIPE_WATCHER_ID, datetime.utcnow().isoformat(),
    )
    watcher = build_submit_watcher(poll_seconds=poll_seconds)

    def _handle_sigint(signum, frame):
        logger.info("SIGINT — stopping watcher")
        watcher.request_stop()

    try:
        signal.signal(signal.SIGINT, _handle_sigint)
    except (ValueError, OSError):
        # Not on the main thread (e.g. some test runners) — Ctrl-C still raises
        # KeyboardInterrupt, which run() catches.
        pass

    watcher.run()


if __name__ == "__main__":
    run()
