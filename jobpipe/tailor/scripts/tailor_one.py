"""
scripts/tailor_one.py — Run the tailor end-to-end for a single job, with optional
Match Agent transcript injection.

This script exists for two reasons:

1. The dashboard's Match Agent chat is currently client-only and not persisted.
   Until that gets wired up properly (`match_chat` column + dashboard write +
   tailor read), this script lets us inject a transcript file by hand for one
   job and still get its insights into the tailored materials.
2. The standard `python3 main.py` polls and processes every approved job. When
   you only want to tailor one (e.g. to iterate on the chat-injection result),
   running this avoids touching the others.

Usage:
    cd job-applicant
    python3 -m scripts.tailor_one --job-id 3705e831db4c8e47 \\
        --transcript output/match_chat_3705e831db4c8e47.txt

    # or skip the transcript and run a "vanilla" tailor for one job:
    python3 -m scripts.tailor_one --job-id <id>

    # add --dry-run to print materials without uploading or status updates:
    python3 -m scripts.tailor_one --job-id <id> --transcript file.txt --dry-run

What it does:
    1. Fetches the job row from Supabase by id.
    2. Reads the transcript file (if any) and attaches it as
       ``job["match_chat_transcript"]`` so the patched tailor prompts pick it up.
    3. Runs the same tailor sequence as ``main.process_approved_jobs``:
       resume tailoring → cover letter → LaTeX render → cover letter PDF.
    4. Resolves the apply URL via ``url_resolver``.
    5. Uploads both PDFs to Supabase Storage (``job-materials`` bucket).
    6. Marks the job ``ready_to_submit`` so the dashboard surfaces it for
       review (skipped under --dry-run).

Failures are loud; the script does not silently retry. Re-run after fixing
the underlying issue (LaTeX install, Storage perms, etc.).
"""

from __future__ import annotations

import argparse
import json
import logging
import sys
from pathlib import Path

# Make the project package importable when invoked as `python3 -m scripts.tailor_one`.
PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from db import client as supabase_client, mark_ready_to_submit, mark_preparing  # noqa: E402
from tailor.resume import tailor_resume  # noqa: E402
from tailor.cover_letter import generate_cover_letter  # noqa: E402
from tailor.cover_letter_pdf import render_cover_letter_pdf  # noqa: E402
from tailor.latex_resume import generate_tailored_latex  # noqa: E402
from applicant.detector import detect_ats, get_applicant  # noqa: E402
from applicant.url_resolver import resolve_application_url  # noqa: E402
from storage import upload_pdf  # noqa: E402

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s — %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
logger = logging.getLogger("tailor_one")


def fetch_job(job_id: str) -> dict:
    res = supabase_client.table("jobs").select("*").eq("id", job_id).execute()
    rows = res.data or []
    if not rows:
        raise SystemExit(f"No job found with id={job_id!r}")
    return rows[0]


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--job-id", required=True, help="jobs.id to tailor")
    parser.add_argument("--transcript", default=None,
                        help="Optional path to a Match Agent transcript file.")
    parser.add_argument("--dry-run", action="store_true",
                        help="Print materials and skip upload + status update.")
    args = parser.parse_args()

    job = fetch_job(args.job_id)
    company = job.get("company") or "Unknown"
    title = job.get("title") or "Unknown"
    print(f"\n=== tailor_one — {company} — {title} (id={job['id']}) ===")
    print(f"  status={job.get('status')}  tier={job.get('tier')}  "
          f"score={job.get('score')}  source={job.get('source')}")
    print(f"  url={job.get('url')}")

    # Inject the Match Agent transcript if provided. The patched tailor
    # prompts (resume.py, cover_letter.py, latex_resume.py) look for
    # ``job["match_chat_transcript"]`` and surface it as authoritative
    # framing for this specific application.
    if args.transcript:
        transcript_path = Path(args.transcript)
        if not transcript_path.exists():
            raise SystemExit(f"transcript file not found: {transcript_path}")
        transcript = transcript_path.read_text(encoding="utf-8")
        job["match_chat_transcript"] = transcript
        print(f"  transcript: {transcript_path} ({len(transcript)} chars) — injected")
    else:
        print("  transcript: (none — running vanilla tailor)")

    if not args.dry_run:
        mark_preparing(job["id"])

    # ── 1. Resume tailoring (metadata) ───────────────────────────────────
    print("\n--- Resume tailoring ---")
    resume_result = tailor_resume(job)
    print(f"summary: {resume_result.get('tailored_summary')}")
    print(f"emphasis: {resume_result.get('emphasis_areas')}")
    print(f"keywords: {resume_result.get('keywords_to_include')}")

    # ── 2. Cover letter text ─────────────────────────────────────────────
    print("\n--- Cover letter ---")
    cover_result = generate_cover_letter(job, resume_result)
    cover_text = cover_result.get("cover_letter", "")
    print(cover_text)

    # ── 3. LaTeX resume PDF ──────────────────────────────────────────────
    print("\n--- LaTeX resume ---")
    latex_result = generate_tailored_latex(job, resume_result)
    if not latex_result.get("compile_success") or not latex_result.get("pdf_bytes"):
        log_excerpt = (latex_result.get("compile_log") or "")[:1000]
        raise SystemExit(f"LaTeX compile failed:\n{log_excerpt}")
    resume_pdf_bytes = latex_result["pdf_bytes"]
    print(f"resume PDF compiled: {len(resume_pdf_bytes)} bytes")

    # ── 4. Cover letter PDF ──────────────────────────────────────────────
    cover_pdf_bytes = render_cover_letter_pdf(
        cover_text, company=company, role=title,
    )
    print(f"cover letter PDF rendered: {len(cover_pdf_bytes)} bytes")

    # ── 5. Resolve apply URL ─────────────────────────────────────────────
    print("\n--- URL resolve ---")
    resolved = resolve_application_url(job.get("url") or "")
    resolved_url = resolved.get("resolved") or job.get("url")
    print(f"resolved: {resolved_url}")
    print(f"is_ats={resolved.get('is_ats')}  notes={resolved.get('notes')}")

    if args.dry_run:
        print("\n[dry-run] skipping Storage upload + status update")
        return

    # ── 6. Upload PDFs to Supabase Storage ───────────────────────────────
    print("\n--- Storage upload ---")
    resume_path = upload_pdf(job["id"], "resume", resume_pdf_bytes)
    cover_path = upload_pdf(job["id"], "cover_letter", cover_pdf_bytes)
    print(f"resume_path={resume_path}")
    print(f"cover_letter_path={cover_path}")

    # ── 7. Mark ready_to_submit ──────────────────────────────────────────
    resolved_ats = detect_ats(resolved_url)
    applicant = get_applicant(resolved_url)
    application_notes = (
        f"ATS: {resolved_ats}\n"
        f"Original URL: {job.get('url')}\n"
        f"Resolved URL: {resolved_url}\n"
        f"Resolver: {resolved.get('notes')}\n"
        f"Auto-submittable: "
        f"{'yes' if applicant else 'no — manual form fill needed'}\n"
        f"Tailored with Match Agent transcript: "
        f"{'yes' if args.transcript else 'no'}"
    )
    resume_summary = json.dumps({
        "tailored_summary": resume_result.get("tailored_summary", ""),
        "emphasis_areas": resume_result.get("emphasis_areas", []),
        "keywords_to_include": resume_result.get("keywords_to_include", []),
        "experience_order": resume_result.get("experience_order", []),
        "suggested_bullets": resume_result.get("suggested_bullets", {}),
        "skills_section": resume_result.get("skills_section", {}),
        "diff_notes": resume_result.get("diff_notes", ""),
        "storage_path": resume_path,
        "compile_success": True,
    })
    mark_ready_to_submit(
        job["id"],
        resume_path=resume_summary,
        cover_letter_path=cover_text,
        application_url=resolved_url,
        application_notes=application_notes,
        resume_pdf_path=resume_path,
        cover_letter_pdf_path=cover_path,
    )
    print(f"\n[ok] job {job['id']} → ready_to_submit")
    print("Visit your dashboard to review and submit.")


if __name__ == "__main__":
    main()
