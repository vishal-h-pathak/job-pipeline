"""
pipeline.py — Tailor Agent entry point (renamed from main.py in PR-4).

Polls Supabase for approved jobs, tailors application materials,
fills forms via Playwright, and pauses for human approval before submission.

Wired as ``jobpipe-tailor = jobpipe.tailor.pipeline:run`` in pyproject.toml
(see :func:`run` at the bottom of the file). Legacy ``python main.py``
invocations should now use ``python pipeline.py`` (or the console script).

Usage:
    jobpipe-tailor                          # Run one cycle
    jobpipe-tailor --status                 # Print current job counts by status
    jobpipe-tailor --test-tailor <job_id>   # Test material generation for a job (no status changes)
"""

from __future__ import annotations

# ── sys.path bootstrap ────────────────────────────────────────────────────
# The tailor subtree uses unprefixed imports (``import db``, ``import config``,
# ``from notify import ...``, ``from storage import ...``,
# ``from tailor.X import ...``, ``from prompts import ...``,
# ``from applicant.X import ...``, ``from interview_prep.X import ...``).
# When this module is imported as ``jobpipe.tailor.pipeline`` (e.g. via the
# ``jobpipe-tailor`` console script), sys.path won't contain
# ``jobpipe/tailor/`` and those bare imports would fail. Insert the
# directory before any other imports run so every downstream module load
# resolves cleanly. PR-4 chose this over a global unprefixed -> qualified
# rewrite to keep the diff scoped, mirroring the pattern PR-3 / PR-5
# established for jobpipe.hunt and jobpipe.submit.
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
from datetime import datetime  # noqa: E402
from pathlib import Path  # noqa: E402

from config import POLL_INTERVAL_MINUTES, HUMAN_APPROVAL_REQUIRED  # noqa: E402
from db import (  # noqa: E402
    get_approved_jobs,
    get_prefill_requested_jobs,
    mark_preparing,
    mark_ready_for_review,
    mark_awaiting_submit,
    mark_applied,
    mark_tailor_failed,
    mark_skipped,
    get_job_counts_by_status,
)
from tailor.resume import tailor_resume  # noqa: E402
from tailor.cover_letter import generate_cover_letter  # noqa: E402
from tailor.cover_letter_pdf import render_cover_letter_pdf  # noqa: E402
from tailor.latex_resume import generate_tailored_latex  # noqa: E402
from tailor.form_answers import generate_form_answers  # noqa: E402
from jobpipe.shared.ats_detect import detect_ats, get_applicant  # noqa: E402
from interview_prep.generator import generate_stories  # noqa: E402
from interview_prep.bank import save_stories  # noqa: E402
from notify import (  # noqa: E402  PR-8: canonical send_* names
    send_awaiting_review,
    send_awaiting_submit,
    send_failed,
)
from storage import (  # noqa: E402
    upload_pdf,
    download_to_tmp,
    upload_prefill_screenshot,
)

# ── Logging ──────────────────────────────────────────────────────────────────
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s — %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
logger = logging.getLogger("pipeline")


def process_approved_jobs():
    """
    Phase 1: Pick up approved jobs, tailor materials, fill forms,
    then pause at ready_to_submit for human review.
    """
    jobs = get_approved_jobs()
    if not jobs:
        return

    logger.info(f"Found {len(jobs)} approved job(s) to process")

    for job in jobs:
        job_id = job["id"]
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
            continue

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
            # Gated on score >= 6 (the existing notify threshold) — below
            # that we won't be applying anyway, so the Sonnet call is
            # wasted. Generation failures are non-fatal.
            score = job.get("score") or 0
            if score >= 6:
                try:
                    form_answers = generate_form_answers(
                        job, resume_result, archetype_meta=archetype_meta
                    )
                    from db import client as _db_client
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
            else:
                logger.info(
                    f"form_answers skipped for {company} (score {score} < 6)"
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


def process_prefill_requested_jobs():
    """
    Phase 2 (M-5): Pick up jobs the user clicked "Pre-fill Form" on,
    open a visible browser, dispatch to the per-ATS handler (Ashby /
    Greenhouse / Lever) or the prepare-only vision agent, capture the
    post-fill screenshot, mark the row `awaiting_human_submit`, and
    BLOCK on terminal input() so the user has time to review what was
    typed, fix anything wrong, and click Submit themselves before the
    browser closes.

    Strictly serial — one job per cycle, no parallelism. The human can
    only review one form at a time.

    M-7 wires this into run_cycle and removes process_confirmed_jobs /
    submit_one_visible / --submit-visible.
    """
    # Lazy imports so the module stays importable without Playwright
    # installed (e.g. for --status / --test-tailor).
    from playwright.sync_api import sync_playwright
    from jobpipe.shared.ats_detect import detect_ats, get_applicant
    from jobpipe.submit.adapters.prepare_dom.universal import UniversalApplicant
    from url_resolver import resolve_application_url
    import json

    jobs = get_prefill_requested_jobs()
    if not jobs:
        return

    logger.info(f"Found {len(jobs)} prefill-requested job(s)")

    for job in jobs:
        job_id = job["id"]
        company = job.get("company", "Unknown")
        title = job.get("title", "Unknown")
        url = (
            job.get("submission_url")
            or job.get("application_url")
            or job.get("url", "")
        )

        logger.info(f"Pre-filling: {company} — {title}  ({url})")

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
            continue

        try:
            tmp_resume_pdf = download_to_tmp(storage_path)
        except Exception as exc:
            mark_tailor_failed(
                job_id,
                f"Pre-fill: resume download failed: {exc}",
                clear_materials=False,
            )
            send_failed(job, f"Pre-fill blocked: {exc}")
            continue

        cover_letter_text = job.get("cover_letter_path") or ""

        try:
            with sync_playwright() as pw:
                browser = pw.chromium.launch(headless=False)
                context = browser.new_context(
                    viewport={"width": 1280, "height": 900},
                    user_agent=(
                        "Mozilla/5.0 (Macintosh; Intel Mac OS X 14_0) "
                        "AppleWebKit/537.36 (KHTML, like Gecko) "
                        "Chrome/121.0 Safari/537.36"
                    ),
                )
                page = context.new_page()
                try:
                    page.goto(
                        real_url, wait_until="domcontentloaded", timeout=45000
                    )
                    try:
                        page.wait_for_load_state("networkidle", timeout=10000)
                    except Exception:
                        pass
                except Exception as exc:
                    browser.close()
                    mark_tailor_failed(
                        job_id,
                        f"Pre-fill: page load failed: {exc}",
                        clear_materials=False,
                    )
                    send_failed(job, f"Pre-fill page load failed: {exc}")
                    continue

                # Per-ATS handlers expose fill_form(page, job, ...).
                # UniversalApplicant exposes apply_with_page (M-5 helper).
                if isinstance(applicant, UniversalApplicant):
                    result = applicant.apply_with_page(
                        page,
                        job,
                        resume_path=str(tmp_resume_pdf),
                        cover_letter_path=cover_letter_text,
                    )
                else:
                    result = applicant.fill_form(
                        page,
                        job,
                        resume_path=str(tmp_resume_pdf),
                        cover_letter_path=cover_letter_text,
                    )

                # Final post-fill screenshot for the cockpit. Persist via
                # Storage so the dashboard can render it via signed URL.
                screenshot_storage_key = None
                try:
                    png_bytes = page.screenshot(full_page=False)
                    screenshot_storage_key = upload_prefill_screenshot(
                        job_id, png_bytes
                    )
                except Exception as exc:
                    logger.warning(
                        f"Could not upload prefill screenshot: {exc}"
                    )

                if result.get("success"):
                    mark_awaiting_submit(
                        job_id, screenshot_path=screenshot_storage_key
                    )
                    send_awaiting_submit(job, screenshot_storage_key)
                else:
                    fail_notes = result.get("notes") or result.get(
                        "review_reason"
                    ) or "pre-fill did not complete cleanly"
                    mark_tailor_failed(
                        job_id,
                        f"Pre-fill: {fail_notes}",
                        clear_materials=False,
                    )
                    send_failed(job, fail_notes)

                # ── BLOCK on terminal input() so the browser stays open ─
                # The human reviews the visible browser, fixes anything
                # wrong, clicks Submit themselves (or copy-pastes from
                # the cockpit's form-answer drafts), then comes back here
                # and presses Enter. The cockpit's "Mark Applied" click
                # is what flips status to applied — never this code.
                bar = "=" * 60
                print(
                    f"\n{bar}\n"
                    f"  Form pre-filled for {company} - {title}\n"
                    f"  ATS: {ats}  ({type(applicant).__name__})\n"
                    f"  Browser is open. Review what was typed, click "
                    f"Submit yourself,\n"
                    f"  then come back to the dashboard and click "
                    f"'Mark Applied'.\n"
                    f"  Press Enter in this terminal to close the browser "
                    f"when done.\n"
                    f"{bar}"
                )
                try:
                    input()
                except (EOFError, KeyboardInterrupt):
                    pass

                browser.close()

        except Exception as exc:
            logger.exception(f"Pre-fill failed for {company}: {exc}")
            mark_tailor_failed(
                job_id,
                f"Pre-fill exception: {exc}",
                clear_materials=False,
            )
            send_failed(job, str(exc))
        finally:
            if tmp_resume_pdf is not None:
                try:
                    Path(tmp_resume_pdf).unlink(missing_ok=True)
                except Exception:
                    pass


def run_cycle():
    """Run one complete poll cycle (M-7).

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
    from db import client as supabase_client
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


if __name__ == "__main__":
    run()
