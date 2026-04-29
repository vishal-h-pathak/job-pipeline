"""
smoke_greenhouse_legacy.py — DEPRECATED M3 validation smoke test.

╔══════════════════════════════════════════════════════════════════════╗
║  LEGACY (Path B). Validates the retired Browserbase + Stagehand      ║
║  Greenhouse adapter against a real Greenhouse URL. Renamed from      ║
║  ``smoke_greenhouse.py`` during the local-Playwright consolidation;  ║
║  no live target invokes it. Kept as reference if the Browserbase     ║
║  fallback ever returns. Do not extend.                               ║
╚══════════════════════════════════════════════════════════════════════╝

Runs the full Greenhouse adapter loop (survey → fill → score) against a real
live Greenhouse posting through Browserbase+Stagehand, but WITHOUT the
database plumbing or the confirm.click_submit_and_verify step. The adapter
will fill the form but never click submit (that's confirm.py's job, which
this script does not invoke).

Use this to validate:
  - .env creds load cleanly
  - Browserbase session can start
  - Playwright can attach over CDP
  - Stagehand extract() returns a usable survey of the GH form
  - Greenhouse adapter fills and scores correctly on a real page

Usage:
    python scripts/smoke_greenhouse.py https://job-boards.greenhouse.io/anthropic/jobs/4899511008
    python scripts/smoke_greenhouse.py  # default target: the Anthropic Android role
"""

from __future__ import annotations

import asyncio
import logging
import os
import sys
import tempfile
from dataclasses import asdict
from pathlib import Path

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from adapters.base import SubmissionContext  # noqa: E402
from adapters.deterministic.greenhouse import GreenhouseAdapter  # noqa: E402  (PR-5)
from browser import session as browser_session  # noqa: E402
from jobpipe import profile_loader  # noqa: E402

logger = logging.getLogger("submitter.smoke")


# Throwaway identity stubs — never sent to a real ATS because we never
# click submit. Smoke-test values only (deliberately invalid email,
# placeholder phone) so the adapter has something to attempt to fill
# with. The canonical form-default answers (work auth, start date,
# relocation, etc.) come from the user-layer YAML via
# `profile_loader.load_application_defaults()` so prose drift in
# CLAUDE.md cannot silently change what the submitter sends.
_SMOKE_IDENTITY_STUBS = {
    "first_name": "Vishal",
    "last_name": "Pathak",
    "email": "smoketest@example.invalid",
    "phone": "+1 555 867 5309",
    "linkedin": "https://www.linkedin.com/in/vishalhpathak/",
    "website": "https://vishal.pa.thak.io",
    "github": "https://github.com/vshlpthk1",
    "location": "Atlanta, GA",
    "current_company": "(smoke test)",
    "current_title": "(smoke test)",
}

FAKE_APPLICANT = {
    **_SMOKE_IDENTITY_STUBS,
    **profile_loader.load_application_defaults(),
}


async def main(url: str) -> int:
    # A real resume PDF — any file will do for smoke purposes since upload
    # attempts on the live page would be caught and scored, not actually sent.
    with tempfile.NamedTemporaryFile(suffix=".pdf", delete=False) as f:
        f.write(b"%PDF-1.4\n%smoke test fake resume\n")
        fake_resume = Path(f.name)
    with tempfile.NamedTemporaryFile(suffix=".pdf", delete=False) as f:
        f.write(b"%PDF-1.4\n%smoke test fake cover letter\n")
        fake_cl = Path(f.name)

    try:
        print(f"[smoke] opening session → {url}")
        async with browser_session.open_session(url) as handle:
            print(f"[smoke] stagehand session: {handle.stagehand_session_id}")
            print(f"[smoke] browserbase session: {handle.browserbase_session_id}")
            print(f"[smoke] REPLAY URL: {handle.browserbase_replay_url}")

            ctx = SubmissionContext(
                job={**FAKE_APPLICANT, "id": "smoke", "url": url},
                resume_pdf_path=fake_resume,
                cover_letter_pdf_path=fake_cl,
                cover_letter_text="Dear hiring team, this is a smoke-test cover letter.",
                application_url=url,
                stagehand_session=handle.stagehand_session,
                page=handle.page,
                attempt_n=0,
            )

            adapter = GreenhouseAdapter()
            print("[smoke] running GreenhouseAdapter.run()...")

            # Smoke budget matches the default SESSION_BUDGET_SECONDS but can
            # be overridden for heavy forms (Anthropic et al) via the env var.
            budget = int(os.environ.get("SMOKE_BUDGET_SECONDS", "600"))
            timed_out = False
            result = None
            try:
                result = await asyncio.wait_for(adapter.run(ctx), timeout=budget)
            except asyncio.TimeoutError:
                timed_out = True
                print(f"[smoke] adapter.run() exceeded {budget}s budget — partial result unavailable")
                print(f"[smoke] replay: {handle.browserbase_replay_url}")

        if timed_out:
            print()
            print("HINT: bump SMOKE_BUDGET_SECONDS (e.g. `SMOKE_BUDGET_SECONDS=900 python scripts/smoke_greenhouse.py <url>`)")
            print("      or pick a smaller Greenhouse posting for first-run validation.")
            return 2

        print()
        print("=" * 60)
        print("RESULT")
        print("=" * 60)
        print(f"  confidence        : {result.confidence:.2f}")
        print(f"  recommend         : {result.recommend}")
        print(f"  recommend_reason  : {result.recommend_reason}")
        print(f"  filled ({len(result.filled_fields)}):")
        for f in result.filled_fields:
            print(f"    - {f.label}: {f.value!r} [{f.kind}] conf={f.confidence:.2f}")
        print(f"  skipped ({len(result.skipped_fields)}):")
        for s in result.skipped_fields:
            print(f"    - {s.label}: {s.reason}")
        if result.error:
            print(f"  ERROR: {result.error}")
        print()
        print(f"  Replay: {handle.browserbase_replay_url}")
        return 0 if result.error is None else 1
    finally:
        for p in (fake_resume, fake_cl):
            try:
                p.unlink()
            except OSError:
                pass


if __name__ == "__main__":
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(name)s %(levelname)s %(message)s",
    )
    default_url = "https://job-boards.greenhouse.io/anthropic/jobs/4899511008"
    target = sys.argv[1] if len(sys.argv) > 1 else default_url
    sys.exit(asyncio.run(main(target)))
