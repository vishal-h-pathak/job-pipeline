"""prepare_dom/universal.py — ATS-agnostic prepare-only applicant (M-4).

Fallback for ATSes without a dedicated DOM handler (Workday, iCIMS,
SmartRecruiters, Indeed, aggregators). Drives a real browser via a
Claude tool-use agent, but the agent has no `click_submit` tool — it
fills the form, calls `finish_preparation`, and the orchestrator
leaves the browser open for the human to review and submit themselves.

The handler:
  - resolves aggregator URLs to real ATS endpoints first (no agent loop)
  - opens a visible (non-headless) browser by default so the human can
    review what got filled
  - feeds the agent the M-1 `form_answers` JSON via the system prompt
    so identity / contact / location values come from profile.yml in
    Python, not from OCR

After M-5 the orchestrator will block on a terminal `input()` after
this returns, keeping the browser context alive for human review.

Moved from ``jobpipe/tailor/applicant/universal.py`` in PR-4.
``BaseApplicant``, ``BrowserSession`` still live under
``jobpipe.tailor.applicant`` (held for PR-7) and resolve via the
tailor sys.path bootstrap fired by ``jobpipe.shared.ats_detect``.
``run_submission_agent`` moved to the sibling ``prepare_loop`` module.
``resolve_application_url`` moved up one level inside tailor.
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Optional

from playwright.sync_api import sync_playwright

from applicant.base import BaseApplicant
from applicant.browser_tools import BrowserSession
from ..prepare_loop import run_submission_agent
from url_resolver import resolve_application_url

logger = logging.getLogger("prepare_dom.universal")


class UniversalApplicant(BaseApplicant):
    """ATS-agnostic prepare-only applicant driven by a Claude tool-use agent."""

    name = "universal"

    def __init__(self, slow_mo_ms: int = 0):
        super().__init__()
        self.slow_mo_ms = slow_mo_ms

    @staticmethod
    def detect(url: str) -> bool:
        # Universal applicant handles anything (used as fallback).
        return True

    def fill_form(self, page, job, resume_path=None, cover_letter_path=None):
        """Kept for BaseApplicant compatibility but apply() is the entry."""
        raise NotImplementedError("UniversalApplicant uses apply() directly")

    @staticmethod
    def _read_cover_letter_text(cover_letter_path_or_text: Optional[str]) -> str:
        if not cover_letter_path_or_text:
            return ""
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

    def apply(
        self,
        job: dict,
        resume_path: Optional[str] = None,
        cover_letter_path: Optional[str] = None,
        headless: bool = False,
    ) -> dict:
        """Run the prepare-only agent. Defaults to a visible browser
        because M-5's orchestrator blocks on a terminal input() after
        this returns so the human can review and submit themselves."""
        url = job.get("application_url") or job.get("url")
        if not url:
            return {
                "success": False,
                "needs_review": True,
                "notes": "No application URL on job",
            }

        # Resolve aggregator → real ATS endpoint (no agent, no LLM call).
        resolved = resolve_application_url(url)
        real_url = resolved["resolved"]
        logger.info(
            f"URL resolved: {url} -> {real_url} "
            f"(is_ats={resolved['is_ats']}, notes={resolved['notes']})"
        )

        cover_letter_text = self._read_cover_letter_text(cover_letter_path)

        slug = "".join(
            c if c.isalnum() else "_" for c in (job.get("company") or "company")
        )[:40]

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
                    resume_path=resume_path,
                    cover_letter_path=None,
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

    def apply_with_page(
        self,
        page,
        job: dict,
        resume_path: Optional[str] = None,
        cover_letter_path: Optional[str] = None,
    ) -> dict:
        """Run the prepare-only agent against a CALLER-managed Playwright page (M-5).

        The orchestrator owns the browser lifecycle so it can keep the
        context alive past `apply_with_page`'s return — the per-ATS
        handlers (`AshbyApplicant.fill_form` etc.) already work this way,
        and `process_prefill_requested_jobs` dispatches uniformly.
        """
        cover_letter_text = self._read_cover_letter_text(cover_letter_path)
        slug = "".join(
            c if c.isalnum() else "_" for c in (job.get("company") or "company")
        )[:40]

        session = BrowserSession(
            page=page,
            resume_path=resume_path,
            cover_letter_path=None,
            cover_letter_text=cover_letter_text,
            job_slug=slug,
        )

        try:
            result = run_submission_agent(
                session=session,
                job=job,
                cover_letter_text=cover_letter_text,
                max_turns=45,
            )
            return result
        except Exception as e:
            logger.exception(f"Universal apply_with_page failed: {e}")
            return {
                "success": False,
                "needs_review": True,
                "review_reason": f"agent exception: {e}",
                "screenshots": session.screenshots,
            }

    # NOTE: no submit() override (M-4). Calls fall through to
    # BaseApplicant.submit() which raises NotImplementedError. The system
    # never auto-submits — the human clicks Submit themselves in the
    # visible browser the orchestrator (M-5) leaves open.
