"""
main.py — Job Applicant Agent entry point.

Polls Supabase for approved jobs, tailors application materials,
fills forms via Playwright, and pauses for human approval before submission.

Usage:
    python main.py                          # Run one cycle
    python main.py --status                 # Print current job counts by status
    python main.py --test-tailor <job_id>   # Test material generation for a job (no status changes)
"""

import sys
import time
import logging
import argparse
from datetime import datetime
from pathlib import Path

from config import POLL_INTERVAL_MINUTES, HUMAN_APPROVAL_REQUIRED
from db import (
    get_approved_jobs,
    get_confirmed_jobs,
    mark_preparing,
    mark_ready_to_submit,
    mark_applied,
    mark_failed,
    get_job_counts_by_status,
)
from tailor.resume import tailor_resume
from tailor.cover_letter import generate_cover_letter
from tailor.cover_letter_pdf import render_cover_letter_pdf
from tailor.latex_resume import generate_tailored_latex
from applicant.detector import detect_ats, get_applicant
from interview_prep.generator import generate_stories
from interview_prep.bank import save_stories
from notify import notify_ready_for_review, notify_applied, notify_failed
from storage import upload_pdf, download_to_tmp, delete_all_for_job

# ── Logging ──────────────────────────────────────────────────────────────────
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s — %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
logger = logging.getLogger("main")


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
            mark_ready_to_submit(
                job_id,
                application_notes="LinkedIn: human-only application required",
            )
            notify_ready_for_review(job)
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
            from applicant.url_resolver import resolve_application_url
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

            # ── Mark ready for review ────────────────────────────────────
            # Save the RESOLVED url so process_confirmed_jobs points the
            # submission agent at the real ATS page, not the aggregator.
            mark_ready_to_submit(
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

            notify_ready_for_review(job)
            logger.info(f"Ready for review: {company} — {title}")

        except Exception as e:
            logger.error(f"Failed to process {company} — {title}: {e}")
            # If upload partially completed, clean so nothing is orphaned.
            try:
                delete_all_for_job(job_id)
            except Exception:
                pass
            mark_failed(job_id, str(e))
            notify_failed(job, str(e))


def process_confirmed_jobs():
    """
    Phase 2: Submit applications that the user has confirmed.

    Pulls the tailored PDF path (stored inside the resume_path JSON blob) and
    the cover letter text (stored verbatim in cover_letter_path), then calls
    the applicant's submit() with both so it can re-fill and click submit.
    """
    jobs = get_confirmed_jobs()
    if not jobs:
        return

    logger.info(f"Found {len(jobs)} confirmed job(s) to submit")

    for job in jobs:
        job_id = job["id"]
        company = job.get("company", "Unknown")
        title = job.get("title", "Unknown")
        url = job.get("application_url") or job.get("url", "")

        logger.info(f"Submitting: {company} — {title}")

        tmp_resume_pdf = None
        try:
            applicant = get_applicant(url)
            if applicant:
                # Resume PDF lives in Supabase Storage. Pull the storage key
                # (new column preferred; fall back to the JSON blob for
                # jobs prepared before the migration).
                import json
                storage_path = job.get("resume_pdf_path")
                if not storage_path:
                    raw_resume = job.get("resume_path") or ""
                    try:
                        resume_meta = json.loads(raw_resume) if raw_resume else {}
                        storage_path = resume_meta.get("storage_path") or resume_meta.get("pdf_path")
                    except Exception:
                        logger.warning(f"Could not parse resume_path for job {job_id}")

                if not storage_path:
                    raise RuntimeError("No resume PDF in Storage or resume_path JSON")

                # Download to a tmp file (ATS form uploads need a real path).
                tmp_resume_pdf = download_to_tmp(storage_path)
                logger.info(f"Downloaded {storage_path} to {tmp_resume_pdf}")

                # Cover letter is stored as raw text in cover_letter_path.
                cover_letter_text = job.get("cover_letter_path") or ""

                result = applicant.submit(
                    job,
                    resume_path=str(tmp_resume_pdf),
                    cover_letter_path=cover_letter_text,
                )
                if result.get("success"):
                    notes = result.get("notes", "")
                    if result.get("screenshot_path"):
                        notes = f"{notes}\nScreenshot: {result['screenshot_path']}"
                    # mark_applied clears Storage by default.
                    mark_applied(job_id, application_notes=notes)
                    notify_applied(job)
                else:
                    failure_notes = result.get("notes", "submission failed")
                    if result.get("screenshot_path"):
                        failure_notes = f"{failure_notes}\nScreenshot: {result['screenshot_path']}"
                    # Don't clear materials on failure — leave them so the user
                    # can retry via the dashboard.
                    mark_failed(job_id, failure_notes)
                    notify_failed(job, failure_notes)
            else:
                # No automated applicant — user confirmed manual application.
                # Treat as "applied manually" and clear materials.
                mark_applied(job_id, application_notes="Applied manually (no automated applicant)")
                notify_applied(job)

        except Exception as e:
            logger.error(f"Submission failed for {company}: {e}")
            mark_failed(job_id, str(e))
            notify_failed(job, str(e))
        finally:
            if tmp_resume_pdf is not None:
                try:
                    Path(tmp_resume_pdf).unlink(missing_ok=True)
                except Exception:
                    pass


def run_cycle():
    """Run one complete poll cycle."""
    logger.info(f"=== Cycle at {datetime.utcnow().isoformat()} ===")
    process_approved_jobs()
    process_confirmed_jobs()


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

    # Summary
    print(f"\n{'='*60}")
    print("  DONE — Review the outputs above.")
    print("  (Test mode writes nothing to disk; run `python main.py` for")
    print("   the real pipeline which uploads to Supabase Storage.)")
    print(f"{'='*60}\n")


def submit_one_visible(job_id: str):
    """
    Submit a single confirmed job in non-headless mode — so a human can watch
    the browser click submit for the first real application.

    Loads the job from Supabase, extracts the stored PDF path + cover letter,
    and calls the applicant's submit() with headless=False.  Updates status
    based on the result (applied / failed).
    """
    from db import client as supabase_client
    import json

    print(f"\n{'='*60}")
    print(f"  SUBMIT (visible) — job_id: {job_id}")
    print(f"{'='*60}\n")

    result = supabase_client.table("jobs").select("*").eq("id", job_id).execute()
    if not result.data:
        print(f"ERROR: No job found with id '{job_id}'")
        return
    job = result.data[0]

    print(f"Job: {job.get('title')} at {job.get('company')}")
    print(f"Status: {job.get('status')} | URL: {job.get('application_url') or job.get('url')}")
    if job.get("status") != "submit_confirmed":
        print(f"WARNING: status is '{job.get('status')}', not 'submit_confirmed'.")
        print("Proceeding anyway, but the normal pipeline expects submit_confirmed.")
    print()

    url = job.get("application_url") or job.get("url", "")
    applicant = get_applicant(url)
    if not applicant:
        print(f"No automated applicant for {detect_ats(url)} — marking applied manually.")
        mark_applied(job["id"], application_notes="Applied manually (no automated applicant)")
        return

    # Resume PDF is in Supabase Storage; pull it to a tmp file.
    storage_path = job.get("resume_pdf_path")
    if not storage_path:
        raw_resume = job.get("resume_path") or ""
        try:
            resume_meta = json.loads(raw_resume) if raw_resume else {}
            storage_path = resume_meta.get("storage_path") or resume_meta.get("pdf_path")
        except Exception:
            pass

    if not storage_path:
        print("ERROR: no resume PDF stored for this job.")
        return

    tmp_resume_pdf = download_to_tmp(storage_path)
    cover_letter_text = job.get("cover_letter_path") or ""

    print(f"Resume PDF: downloaded {storage_path} → {tmp_resume_pdf}")
    print(f"Cover letter: {len(cover_letter_text)} chars")
    print("Opening browser (non-headless)...")

    sub_result = applicant.submit(
        job,
        resume_path=str(tmp_resume_pdf),
        cover_letter_path=cover_letter_text,
        headless=False,
    )
    try:
        Path(tmp_resume_pdf).unlink(missing_ok=True)
    except Exception:
        pass

    print(f"\n{'='*60}")
    print(f"  RESULT: success={sub_result.get('success')}, submitted={sub_result.get('submitted')}")
    print(f"{'='*60}")
    print(f"Screenshot: {sub_result.get('screenshot_path')}")
    print(f"Notes:\n{sub_result.get('notes', '')}\n")

    if sub_result.get("success"):
        notes = sub_result.get("notes", "")
        if sub_result.get("screenshot_path"):
            notes = f"{notes}\nScreenshot: {sub_result['screenshot_path']}"
        mark_applied(job["id"], application_notes=notes)
        notify_applied(job)
        print("-> Job marked as applied.")
    else:
        failure_notes = sub_result.get("notes", "submission failed")
        if sub_result.get("screenshot_path"):
            failure_notes = f"{failure_notes}\nScreenshot: {sub_result['screenshot_path']}"
        mark_failed(job["id"], failure_notes)
        notify_failed(job, failure_notes)
        print("-> Job marked as failed.")


def main():
    parser = argparse.ArgumentParser(description="Job Applicant Agent")
    parser.add_argument("--once", action="store_true", help="Run one cycle and exit")
    parser.add_argument("--status", action="store_true", help="Print job counts by status")
    parser.add_argument("--test-tailor", metavar="JOB_ID",
                        help="Test material generation for a job (no status changes)")
    parser.add_argument("--submit-visible", metavar="JOB_ID",
                        help="Submit a single confirmed job in non-headless mode so "
                             "a human can watch the browser click submit")
    args = parser.parse_args()

    if args.status:
        print_status()
        return

    if args.test_tailor:
        test_tailor(args.test_tailor)
        return

    if args.submit_visible:
        submit_one_visible(args.submit_visible)
        return

    print(f"""
╔═══════════════════════════════════════════════╗
║       JOB APPLICANT AGENT                     ║
║  {datetime.utcnow().strftime('%Y-%m-%d %H:%M:%S')} UTC{' ' * 21}║
║  Poll interval: {POLL_INTERVAL_MINUTES} min{' ' * 24}║
║  Human approval: {'ON' if HUMAN_APPROVAL_REQUIRED else 'OFF'}{' ' * 24}║
╚═══════════════════════════════════════════════╝
""")

    # Always run a single cycle. Use --once for clarity, but bare `python main.py` also runs once.
    # The polling loop has been removed to prevent unattended API usage.
    # To run on a schedule, use an external scheduler (cron, Cowork scheduled task, etc.)
    run_cycle()


if __name__ == "__main__":
    main()
