"""
scripts/test_beacon.py — End-to-end smoke test for UniversalApplicant on Beacon.

Uses the resume PDF and cover letter already generated in output/ and points
the applicant at the real Beacon Greenhouse posting. Runs in PREPARE mode with
a visible browser (headed) so you can watch and verify before the actual submit.

Usage:
  python -m scripts.test_beacon            # prepare mode, headed
  python -m scripts.test_beacon --submit   # submit mode after you've reviewed
  python -m scripts.test_beacon --headless # run invisibly
"""

from __future__ import annotations

import argparse
import json
import logging
import os
import sys
from pathlib import Path

os.environ.setdefault("SSL_CERT_FILE", "/etc/ssl/certs/ca-certificates.crt")
os.environ.setdefault("REQUESTS_CA_BUNDLE", "/etc/ssl/certs/ca-certificates.crt")

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from dotenv import load_dotenv
load_dotenv(ROOT / ".env")

from jobpipe.submit.adapters.prepare_dom.universal import UniversalApplicant

BEACON_JOB = {
    "id": "beacon-neuroscientist-esp-2026",
    "title": "Neuroscientist, External Scientific Programs",
    "company": "Beacon Biosignals",
    "location": "Boston, MA - Remote (US preferred)",
    "url": "https://boards.greenhouse.io/beaconbiosignals/jobs/4110972009",
    "application_url": "https://boards.greenhouse.io/beaconbiosignals/jobs/4110972009",
    "description": (
        "Beacon Biosignals builds at-home EEG platforms (Waveband, FDA 510(k) cleared) "
        "that support clinical development of novel therapeutics for neurological, "
        "psychiatric, and sleep disorders. Seeking an experienced neuroscientist/data "
        "scientist to serve as scientific partner to Life Science customers (pharma/biotech), "
        "analyzing clinical trial EEG data and presenting results to external stakeholders. "
        "Requires multi-year neuroscience + statistics experience, customer-facing "
        "comfort, version-controlled collaborative coding. Familiarity with Julia, AWS, "
        "Superset, Pandoc, SQL, GraphQL a plus. Salary $135k-$155k."
    ),
}


def _latest(glob: str) -> str | None:
    matches = sorted(Path(ROOT / "output").glob(glob))
    return str(matches[-1]) if matches else None


def main():
    p = argparse.ArgumentParser()
    p.add_argument("--submit", action="store_true",
                   help="Run in SUBMIT mode (will actually send the application!)")
    p.add_argument("--headless", action="store_true",
                   help="Run without a visible browser window")
    p.add_argument("--slow-mo", type=int, default=150,
                   help="Milliseconds between actions (helps you watch, default 150)")
    args = p.parse_args()

    logging.basicConfig(level=logging.INFO,
                        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s")

    resume_pdf = _latest("resume_Beacon_Biosignals_*.pdf")
    cover_letter = _latest("cover_letter_Beacon_Biosignals_*.txt")

    if not resume_pdf:
        raise SystemExit("No Beacon resume PDF found in output/. Run tailoring first.")
    if not cover_letter:
        raise SystemExit("No Beacon cover letter found in output/. Run tailoring first.")

    cover_text = Path(cover_letter).read_text(encoding="utf-8")

    print(f"mode:         {'SUBMIT' if args.submit else 'PREPARE'}")
    print(f"url:          {BEACON_JOB['application_url']}")
    print(f"resume:       {resume_pdf}")
    print(f"cover letter: {cover_letter} ({len(cover_text)} chars)")
    print(f"headless:     {args.headless}")
    print()
    if args.submit:
        print("!! SUBMIT MODE — this will actually send the application.")
        confirm = input("type YES to continue: ")
        if confirm.strip() != "YES":
            print("aborted.")
            return

    applicant = UniversalApplicant(slow_mo_ms=args.slow_mo)
    if args.submit:
        result = applicant.submit(
            BEACON_JOB, resume_pdf, cover_text, headless=args.headless,
        )
    else:
        result = applicant.apply(
            BEACON_JOB, resume_pdf, cover_text, headless=args.headless,
        )

    print("\n── RESULT ──")
    print(json.dumps({k: v for k, v in result.items() if k != "screenshots"},
                     indent=2, default=str))
    print(f"\nScreenshots ({len(result.get('screenshots', []))}):")
    for s in result.get("screenshots", []):
        print(f"  {s}")


if __name__ == "__main__":
    main()
