"""
scripts/submit_one.py — Run the universal applicant on one job.

Usage:
  python -m scripts.submit_one --job-id <supabase_job_id> --mode prepare
  python -m scripts.submit_one --job-id <supabase_job_id> --mode submit

Or, Supabase-free mode (supply everything by hand):
  python -m scripts.submit_one \
    --url https://boards.greenhouse.io/.../jobs/123 \
    --company "Beacon Biosignals" \
    --title "Neuroscientist" \
    --resume /path/to/resume.pdf \
    --cover-letter /path/to/cover.txt \
    --job-description /path/to/jd.txt \
    --mode prepare \
    --headed
"""

from __future__ import annotations

import argparse
import json
import logging
import os
import sys
from pathlib import Path

# Proxy CA handling for sandbox — only set if the file actually exists.
# On macOS these Linux paths don't exist and httpx will crash trying to
# load them. Leave env alone on Mac so httpx falls back to certifi.
_SANDBOX_CA = "/etc/ssl/certs/ca-certificates.crt"
if Path(_SANDBOX_CA).exists():
    os.environ.setdefault("SSL_CERT_FILE", _SANDBOX_CA)
    os.environ.setdefault("REQUESTS_CA_BUNDLE", _SANDBOX_CA)

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from dotenv import load_dotenv
load_dotenv(ROOT / ".env")

from applicant.universal import UniversalApplicant

logger = logging.getLogger("submit_one")


def _load_job_from_supabase(job_id: str) -> dict:
    from db import client as sb
    result = sb.table("jobs").select("*").eq("id", job_id).execute()
    if not result.data:
        raise SystemExit(f"No job in Supabase with id={job_id}")
    return result.data[0]


def _materials_from_job(job: dict) -> tuple[str, str]:
    """
    Extract the PDF path and cover-letter text from the job record.
    Phase-1 stored a JSON blob in resume_path and raw text in cover_letter_path.
    Phase-2 stores the PDF in Supabase Storage at resume_pdf_path.
    """
    resume_pdf = None
    cover_text = ""

    # Phase-2 path: download from Supabase Storage into a tempfile
    storage_key = job.get("resume_pdf_path")
    if storage_key:
        try:
            from storage import download_to_tmp
            tmp_path = download_to_tmp(storage_key)
            resume_pdf = str(tmp_path)
            logger.info(f"Downloaded resume from Storage: {storage_key} → {resume_pdf}")
        except Exception as e:
            logger.warning(f"Failed to download resume from Storage ({storage_key}): {e}")

    # Phase-1 fallback: filesystem path embedded in resume_path JSON
    if not resume_pdf:
        raw_resume = job.get("resume_path")
        if raw_resume:
            try:
                if raw_resume.strip().startswith("{"):
                    parsed = json.loads(raw_resume)
                    resume_pdf = parsed.get("pdf_path") or parsed.get("output_path")
                else:
                    resume_pdf = raw_resume
            except Exception:
                resume_pdf = raw_resume

    raw_cover = job.get("cover_letter_path")
    if raw_cover:
        # cover_letter_path can be either a filesystem path OR the full text
        # of the letter. Guard against stat() on an oversized string (macOS
        # raises ENAMETOOLONG > 255 chars) and against strings that clearly
        # aren't paths (contain newlines).
        looks_like_path = (
            len(raw_cover) < 255
            and "\n" not in raw_cover
            and raw_cover.strip().startswith(("/", "~", "."))
        )
        if looks_like_path:
            try:
                if Path(raw_cover).expanduser().exists():
                    cover_text = Path(raw_cover).expanduser().read_text(encoding="utf-8")
                else:
                    cover_text = raw_cover
            except OSError:
                cover_text = raw_cover
        else:
            cover_text = raw_cover

    return resume_pdf, cover_text


def main():
    p = argparse.ArgumentParser()
    p.add_argument("--mode", choices=["prepare", "submit"], required=True)
    p.add_argument("--job-id", help="Supabase job id")
    p.add_argument("--url", help="Application URL (standalone mode)")
    p.add_argument("--company", help="Company name (standalone mode)")
    p.add_argument("--title", help="Job title (standalone mode)")
    p.add_argument("--resume", help="Path to tailored resume PDF (standalone mode)")
    p.add_argument("--cover-letter", help="Path to cover letter .txt (standalone mode)")
    p.add_argument("--job-description", help="Path to JD text file (standalone mode)")
    p.add_argument("--headed", action="store_true", help="Run browser with a visible window")
    p.add_argument("--slow-mo", type=int, default=0, help="Slow-mo ms between actions")
    p.add_argument("--dry-run", action="store_true", help="Skip Supabase writes")
    args = p.parse_args()

    logging.basicConfig(level=logging.INFO,
                        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s")

    if args.job_id:
        job = _load_job_from_supabase(args.job_id)
        resume_pdf, cover_text = _materials_from_job(job)
    else:
        if not (args.url and args.company and args.title and args.resume):
            raise SystemExit("Standalone mode requires --url --company --title --resume")
        jd = ""
        if args.job_description and Path(args.job_description).exists():
            jd = Path(args.job_description).read_text(encoding="utf-8")
        cover_text = ""
        if args.cover_letter and Path(args.cover_letter).exists():
            cover_text = Path(args.cover_letter).read_text(encoding="utf-8")
        job = {
            "id": "standalone",
            "url": args.url,
            "application_url": args.url,
            "company": args.company,
            "title": args.title,
            "description": jd,
        }
        resume_pdf = args.resume

    applicant = UniversalApplicant(slow_mo_ms=args.slow_mo)

    print(f"\n>> {args.mode} mode on {job.get('company')}: {job.get('title')}")
    print(f"   URL: {job.get('application_url') or job.get('url')}")
    print(f"   Resume: {resume_pdf}")
    print(f"   Cover letter: {len(cover_text)} chars")

    if args.mode == "prepare":
        result = applicant.apply(job, resume_pdf, cover_text, headless=not args.headed)
    else:
        result = applicant.submit(job, resume_pdf, cover_text, headless=not args.headed)

    print("\n── RESULT ──────────────────────────────")
    print(json.dumps({k: v for k, v in result.items() if k != "screenshots"},
                     indent=2, default=str))
    print(f"\nScreenshots ({len(result.get('screenshots', []))}):")
    for s in result.get("screenshots", []):
        print(f"  {s}")

    # Supabase state transitions
    if args.job_id and not args.dry_run:
        from db import mark_applied, mark_failed, mark_needs_review
        if args.mode == "prepare":
            if result.get("needs_review"):
                mark_needs_review(
                    args.job_id,
                    reason=result.get("review_reason") or "agent paused",
                    screenshot_path=(result.get("screenshots") or [None])[-1],
                    uncertain_fields=result.get("uncertain_fields"),
                )
                print("\n→ Supabase: status=needs_review")
            elif result.get("success"):
                # Already ready_to_submit from Phase 1; nothing to do here.
                print("\n→ Supabase: (ready_to_submit set at Phase 1; no change)")
        elif args.mode == "submit":
            if result.get("submitted"):
                mark_applied(args.job_id,
                             application_notes=result.get("submit_confirmation_text"))
                print("\n→ Supabase: status=applied")
            elif result.get("needs_review"):
                mark_needs_review(
                    args.job_id,
                    reason=result.get("review_reason") or "submit paused",
                    screenshot_path=(result.get("screenshots") or [None])[-1],
                )
                print("\n→ Supabase: status=needs_review")
            else:
                mark_failed(args.job_id, reason="submit did not complete")
                print("\n→ Supabase: status=failed")


if __name__ == "__main__":
    main()
