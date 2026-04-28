"""prepare_dom/greenhouse.py — Greenhouse ATS DOM-based form filler (M-3).

Greenhouse hosts forms at boards.greenhouse.io / job-boards.greenhouse.io
/ apply.greenhouse.io. The forms are server-rendered HTML with stable
``name`` attributes like ``job_application[first_name]``, so we prefer those
over label-based heuristics. Uses ``job["form_answers"]`` (M-1) as the
authoritative source of identity / contact / location values — zero
Anthropic API calls.

Same shape as ``prepare_dom/ashby.py``: static ``detect()``, ``fill_form()``
returning ``{success, screenshot_path, notes, fields_filled}``. Does NOT
click Submit. After M-5 the orchestrator screenshots, marks the row
``awaiting_human_submit``, and blocks on terminal ``input()`` while the
human reviews the visible browser.

Known M-3 limitation: role-specific custom questions (rendered as
``job_application[answers_attributes][N][text_value]``) are NOT auto-filled
here — their wording varies enough that the safer path is to let the human
paste the draft answers from ``form_answers.additional_questions`` via the
cockpit copy buttons. The PR-7 helper
``_common.note_unfilled_custom_questions`` surfaces the operator-facing
note.

PR-7 history: shared sync Playwright helpers moved to
``prepare_dom/_common.py``. This file keeps Greenhouse-specific knowledge:
the ``_GREENHOUSE_NAME_MAP`` (canonical input ``name`` per label) plus the
Greenhouse-specific resume/cover-letter selector lists. The
``BaseApplicant`` import is now the explicit
``jobpipe.submit.adapters.applicant_base`` path.
"""

import logging
import time
from pathlib import Path

from jobpipe.submit.adapters.applicant_base import BaseApplicant
from ._common import (
    build_field_map,
    fill_text,
    label_selectors,
    load_cover_letter,
    name_attr_selectors,
    note_unfilled_custom_questions,
    paste_textarea,
    upload_file,
)

logger = logging.getLogger("prepare_dom.greenhouse")


# Greenhouse form fields are prefixed ``job_application[...]`` so the
# selector strategy is name-attr-first, label-second.
_GREENHOUSE_NAME_MAP = {
    "First Name": "job_application[first_name]",
    "Last Name": "job_application[last_name]",
    "Email": "job_application[email]",
    "Phone": "job_application[phone]",
    "LinkedIn URL": "job_application[urls][LinkedIn]",
    "LinkedIn": "job_application[urls][LinkedIn]",
    "GitHub URL": "job_application[urls][GitHub]",
    "GitHub": "job_application[urls][GitHub]",
    "Portfolio": "job_application[urls][Portfolio]",
    "Website": "job_application[urls][Website]",
    "Current Company": "job_application[company]",
    "Current Title": "job_application[title]",
    "Location": "job_application[location]",
    "Current Location": "job_application[location]",
    "City": "job_application[location]",
}

_GREENHOUSE_RESUME_SELECTORS = [
    'input[type="file"][name="job_application[resume]"]',
    'input[type="file"][name*="resume" i]',
    'input[type="file"][accept*=".pdf"]',
    'input[type="file"]',
]

_GREENHOUSE_COVER_LETTER_SELECTORS = [
    'textarea[name="job_application[cover_letter]"]',
    'textarea[name*="cover" i]',
    'textarea[aria-label*="cover" i]',
    'textarea[placeholder*="cover" i]',
    "textarea",
]


class GreenhouseApplicant(BaseApplicant):
    """Playwright-based DOM form filler for Greenhouse ATS applications."""

    name: str = "greenhouse"

    # ── Detection ────────────────────────────────────────────────────────────

    @staticmethod
    def detect(url: str) -> bool:
        """Return True for Greenhouse-hosted application URLs."""
        url_lower = (url or "").lower()
        return (
            "boards.greenhouse.io" in url_lower
            or "job-boards.greenhouse.io" in url_lower
            or "apply.greenhouse.io" in url_lower
            or "greenhouse.io/embed/job_app" in url_lower
        )

    # ── Form filling ─────────────────────────────────────────────────────────

    def fill_form(
        self,
        page,
        job: dict,
        resume_path: str = None,
        cover_letter_path: str = None,
    ) -> dict:
        """Fill a Greenhouse application form from ``job["form_answers"]``."""
        filled = []
        notes_parts = []

        try:
            page.wait_for_load_state("networkidle", timeout=15000)
            time.sleep(1)

            field_map = build_field_map(job)
            for label_text, value in field_map.items():
                if not value:
                    continue
                selectors = (
                    name_attr_selectors(_GREENHOUSE_NAME_MAP, label_text)
                    + label_selectors(label_text)
                )
                if fill_text(page, selectors, value, log=logger):
                    filled.append(label_text)

            notes_parts.append(
                f"Filled fields: {', '.join(filled) if filled else 'none'}"
            )

            # Resume upload
            if resume_path and Path(resume_path).exists():
                if upload_file(page, _GREENHOUSE_RESUME_SELECTORS, resume_path, log=logger):
                    notes_parts.append(
                        f"Uploaded resume: {Path(resume_path).name}"
                    )
                else:
                    notes_parts.append("Resume upload: no file input found")
            elif resume_path:
                notes_parts.append(f"Resume path not found: {resume_path}")

            # Cover letter
            if cover_letter_path:
                cover_text = load_cover_letter(cover_letter_path)
                if cover_text and paste_textarea(
                    page, _GREENHOUSE_COVER_LETTER_SELECTORS, cover_text, log=logger
                ):
                    notes_parts.append("Pasted cover letter")
                elif cover_text:
                    notes_parts.append("Cover letter: no textarea found")

            # Custom questions are intentionally NOT auto-filled here.
            # They live in form_answers.additional_questions; the human
            # pastes them from the cockpit via copy buttons.
            note_unfilled_custom_questions(job, notes_parts)

            screenshot_path = self.take_screenshot(
                page, label=f"greenhouse_{job.get('id', 'unknown')}"
            )
            notes_parts.append(f"Screenshot: {screenshot_path}")

            return {
                "success": len(filled) > 0,
                "screenshot_path": screenshot_path,
                "notes": "\n".join(notes_parts),
                "fields_filled": filled,
            }

        except Exception as e:
            logger.error(f"Greenhouse form fill error: {e}")
            return {
                "success": False,
                "notes": (
                    f"Error during form fill: {e}\n"
                    f"Partial: {', '.join(notes_parts)}"
                ),
            }
