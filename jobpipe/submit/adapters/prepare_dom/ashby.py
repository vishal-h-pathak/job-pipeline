"""prepare_dom/ashby.py — Ashby ATS (ashbyhq.com) DOM-based form filler (M-3).

Navigates to an Ashby-hosted application page, fills standard fields by
reading values from ``job["form_answers"]`` (the structured JSON written by
the M-1 tailoring step), uploads a resume PDF, pastes a cover letter, takes
a screenshot, and returns. Zero Anthropic API calls — pure Playwright + DOM
selectors.

The handler does NOT click Submit. After M-3 + M-5, the orchestrator takes
the post-fill screenshot, marks the row ``awaiting_human_submit``, and blocks
on a terminal ``input()`` while the human reviews the visible browser, fixes
anything wrong, clicks Submit themselves, and then comes back to the
dashboard cockpit to click "Mark Applied".

PR-7 history: shared sync Playwright helpers (selector iteration, file
upload, textarea paste, cover-letter resolution, field-map construction) now
live in ``prepare_dom/_common.py``. Part A (#3) then moved the Ashby field
*definitions* into ``field_maps.yml`` under the ``ashby`` key — including the
fuzzy ``input[name*="..."]`` fallbacks (via the per-ATS
``defaults: {fuzzy_name_fallback: true}`` block, since Ashby has no canonical
name map) and the union of cover-letter textarea selectors (with the
``div[contenteditable="true"]`` rich-text fallback). This adapter keeps only
the Ashby-specific *behaviour*: the overview->``/application`` URL hop and the
longer SPA-hydration wait. The ``BaseApplicant`` import is the explicit
``jobpipe.submit.adapters.applicant_base`` path.
"""

import logging
import time
from urllib.parse import urlparse, urlunparse

from jobpipe.submit.adapters.applicant_base import BaseApplicant
from .field_maps import run_field_map_fill

logger = logging.getLogger("prepare_dom.ashby")


class AshbyApplicant(BaseApplicant):
    """Playwright-based DOM form filler for Ashby ATS applications."""

    name: str = "ashby"

    # ── Detection ────────────────────────────────────────────────────────────

    @staticmethod
    def detect(url: str) -> bool:
        """Return True if the URL points to an Ashby-hosted application."""
        url_lower = (url or "").lower()
        return (
            "ashbyhq.com" in url_lower
            or "ashby_jid" in url_lower
            or "jobs.ashby" in url_lower
        )

    # ── Form filling ─────────────────────────────────────────────────────────

    def fill_form(
        self,
        page,
        job: dict,
        resume_path: str = None,
        cover_letter_path: str = None,
    ) -> dict:
        """Fill an Ashby application form from ``job["form_answers"]``.

        Ashby renders inputs inside a React app. Most labels are explicit
        ``<label>`` elements; some use ``aria-label``; some use placeholders.
        The ``ashby`` field map tries multiple selector strategies per field
        (label/aria/placeholder + the fuzzy ``input[name*=...]`` fallback) and
        stops at the first match. Unlike Greenhouse / Lever, Ashby does NOT
        emit the custom-questions note (parity with the pre-rewrite adapter).
        """
        try:
            # Ashby URLs from the hunt are typically the overview page
            # (jobs.ashbyhq.com/{org}/{job_id}); the application form lives
            # at /{org}/{job_id}/application. Without this hop the surveyor
            # finds an empty page and returns success=False. Idempotent —
            # if the URL already ends in /application, no extra goto.
            current = page.url
            parsed = urlparse(current)
            path = parsed.path.rstrip("/")
            if not path.endswith("/application"):
                new_path = path + "/application"
                target = urlunparse(parsed._replace(path=new_path))
                logger.info(
                    f"ashby: navigating from overview to form: {target}"
                )
                page.goto(
                    target, wait_until="domcontentloaded", timeout=45000
                )

            page.wait_for_load_state("networkidle", timeout=15000)
            time.sleep(2)  # extra buffer for React hydration

            return run_field_map_fill(
                self, page, job, "ashby",
                screenshot_label=f"ashby_{job.get('id', 'unknown')}",
                resume_path=resume_path,
                cover_letter_path=cover_letter_path,
                note_custom_questions=False,
                log=logger,
            )

        except Exception as e:
            logger.error(f"Ashby form fill error: {e}")
            return {
                "success": False,
                "notes": f"Error during form fill: {e}",
                "fields_filled": [],
                "required_empty": [],
            }
