"""
applicant/universal.py — ATS-agnostic applicant driven by a Claude tool-use agent.

One class handles any ATS (Greenhouse, Lever, Ashby, Workday, iCIMS, Smart-
Recruiters, ...) because the agent sees the live page and fills fields by label
rather than by hard-coded selectors. URL aggregators are resolved first.

Flow:
  apply(job, resume_path, cover_letter_path, headless) → prepare-mode run,
    filling the form and stopping short of submit. Produces:
      success, screenshot_path, notes, needs_review, review_reason.

  submit(job, resume_path, cover_letter_path, headless) → submit-mode run,
    re-filling the form from scratch and clicking submit.
"""

from __future__ import annotations

import logging
import time
from datetime import datetime
from pathlib import Path
from typing import Optional

from playwright.sync_api import sync_playwright

from applicant.base import BaseApplicant
from applicant.browser_tools import BrowserSession
from applicant.agent_loop import run_submission_agent
from applicant.url_resolver import resolve_application_url
from config import OUTPUT_DIR

logger = logging.getLogger("applicant.universal")


class UniversalApplicant(BaseApplicant):
    """
    ATS-agnostic applicant driven by a Claude tool-use agent. Replaces per-ATS
    applicant classes.
    """

    name = "universal"

    def __init__(self, slow_mo_ms: int = 0):
        super().__init__()
        self.slow_mo_ms = slow_mo_ms

    @staticmethod
    def detect(url: str) -> bool:
        # Universal applicant handles anything
        return True

    def fill_form(self, page, job, resume_path=None, cover_letter_path=None):
        """Kept for BaseApplicant compatibility but we override apply/submit below."""
        raise NotImplementedError("UniversalApplicant uses apply()/submit() directly")

    # ── internal: read cover letter text from file if present ─────────────
    @staticmethod
    def _read_cover_letter_text(cover_letter_path_or_text: Optional[str]) -> str:
        if not cover_letter_path_or_text:
            return ""
        # Heuristic: if it's long, multi-line, or lacks a file extension,
        # treat it as raw text rather than a path (avoids OSError on Path ops).
        s = cover_letter_path_or_text
        looks_like_path = (
            len(s) < 1024
            and "\n" not in s
            and s.endswith((".txt", ".md", ".pdf", ".docx"))
        )
        if looks_like_path:
            try:
                p = Path(s)
                if p.exists():
                    return p.read_text(encoding="utf-8")
            except Exception:
                pass
        return s

    # ── one shared driver for prepare & submit ────────────────────────────
    def _run(
        self,
        job: dict,
        resume_path: str,
        cover_letter_path: str,
        mode: str,
        headless: bool = True,
    ) -> dict:
        url = job.get("application_url") or job.get("url")
        if not url:
            return {"success": False, "needs_review": True,
                    "notes": "No application URL on job"}

        # Resolve aggregator → real ATS
        resolved = resolve_application_url(url)
        real_url = resolved["resolved"]
        logger.info(f"URL resolved: {url} → {real_url} (is_ats={resolved['is_ats']}, notes={resolved['notes']})")

        cover_letter_text = self._read_cover_letter_text(cover_letter_path)

        # Determine slug for filenames
        slug = "".join(c if c.isalnum() else "_" for c in (job.get("company") or "company"))[:40]

        try:
            with sync_playwright() as pw:
                browser = pw.chromium.launch(
                    headless=headless,
                    slow_mo=self.slow_mo_ms,
                )
                context = browser.new_context(
                    viewport={"width": 1280, "height": 900},
                    user_agent=(
                        "Mozilla/5.0 (Macintosh; Intel Mac OS X 14_0) AppleWebKit/537.36 "
                        "(KHTML, like Gecko) Chrome/121.0 Safari/537.36"
                    ),
                )
                page = context.new_page()
                try:
                    page.goto(real_url, wait_until="domcontentloaded", timeout=45000)
                    try:
                        page.wait_for_load_state("networkidle", timeout=10000)
                    except Exception:
                        pass
                except Exception as e:
                    browser.close()
                    return {
                        "success": False,
                        "needs_review": True,
                        "review_reason": f"Failed to load {real_url}: {e}",
                        "screenshots": [],
                    }

                session = BrowserSession(
                    page=page,
                    mode=mode,
                    resume_path=resume_path,
                    cover_letter_path=None,  # prefer inline paste for the letter
                    cover_letter_text=cover_letter_text,
                    job_slug=slug,
                )

                result = run_submission_agent(
                    session=session,
                    job=job,
                    cover_letter_text=cover_letter_text,
                    max_turns=45,
                )

                browser.close()
                result["resolved_url"] = real_url
                result["url_trail"] = resolved["trail"]
                return result
        except Exception as e:
            logger.exception(f"Universal applicant failed: {e}")
            return {
                "success": False,
                "needs_review": True,
                "review_reason": f"driver exception: {e}",
                "screenshots": [],
            }

    def apply(self, job, resume_path=None, cover_letter_path=None, headless=True):
        """Prepare mode — fill the form and stop before submit."""
        return self._run(job, resume_path, cover_letter_path, mode="prepare", headless=headless)

    def submit(self, job, resume_path=None, cover_letter_path=None, headless=True):
        """Submit mode — fill then click submit and confirm."""
        return self._run(job, resume_path, cover_letter_path, mode="submit", headless=headless)
