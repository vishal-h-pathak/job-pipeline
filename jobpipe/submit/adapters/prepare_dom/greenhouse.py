"""prepare_dom/greenhouse.py — Greenhouse ATS DOM-based form filler (M-3).

Greenhouse hosts forms at boards.greenhouse.io / job-boards.greenhouse.io
/ apply.greenhouse.io. The forms are server-rendered HTML with stable
`name` attributes like `job_application[first_name]`, so we prefer
those over label-based heuristics. Uses `job["form_answers"]` (M-1)
as the authoritative source of identity / contact / location values —
zero Anthropic API calls.

Same shape as `prepare_dom/ashby.py`: static `detect()`, `fill_form()`
returning `{success, screenshot_path, notes, fields_filled}`. Does NOT
click Submit. After M-5 the orchestrator screenshots, marks the row
`awaiting_human_submit`, and blocks on terminal `input()` while the
human reviews the visible browser.

Known M-3 limitation: role-specific custom questions (rendered as
`job_application[answers_attributes][N][text_value]`) are NOT auto-
filled here — their wording varies enough that the safer path is to
let the human paste the draft answers from
`form_answers.additional_questions` via the cockpit copy buttons.

Moved from ``jobpipe/tailor/applicant/greenhouse.py`` in PR-4. Sibling
``_build_field_map`` import switched to relative ``from .ashby``.
"""

import logging
import time
from pathlib import Path

from playwright.sync_api import Page

from .ashby import _build_field_map
from applicant.base import BaseApplicant

logger = logging.getLogger("prepare_dom.greenhouse")


# Greenhouse form fields are prefixed `job_application[...]` so the
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
        page: Page,
        job: dict,
        resume_path: str = None,
        cover_letter_path: str = None,
    ) -> dict:
        """Fill a Greenhouse application form from `job["form_answers"]`."""
        filled = []
        notes_parts = []

        try:
            page.wait_for_load_state("networkidle", timeout=15000)
            time.sleep(1)

            field_map = _build_field_map(job)
            for label_text, value in field_map.items():
                if not value:
                    continue
                if self._try_fill_field(page, label_text, value):
                    filled.append(label_text)

            notes_parts.append(
                f"Filled fields: {', '.join(filled) if filled else 'none'}"
            )

            # Resume upload
            if resume_path and Path(resume_path).exists():
                if self._try_upload_resume(page, resume_path):
                    notes_parts.append(
                        f"Uploaded resume: {Path(resume_path).name}"
                    )
                else:
                    notes_parts.append("Resume upload: no file input found")
            elif resume_path:
                notes_parts.append(f"Resume path not found: {resume_path}")

            # Cover letter
            if cover_letter_path:
                cover_text = self._load_cover_letter(cover_letter_path)
                if cover_text and self._try_paste_cover_letter(page, cover_text):
                    notes_parts.append("Pasted cover letter")
                elif cover_text:
                    notes_parts.append("Cover letter: no textarea found")

            # Custom questions are intentionally NOT auto-filled here.
            # They live in form_answers.additional_questions; the human
            # pastes them from the cockpit via copy buttons.
            qs = (job.get("form_answers") or {}).get("additional_questions") or []
            if qs:
                notes_parts.append(
                    f"{len(qs)} role-specific question(s) NOT auto-filled - "
                    f"paste from cockpit drafts"
                )

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

    # ── Private helpers ──────────────────────────────────────────────────────

    def _try_fill_field(self, page: Page, label_text: str, value: str) -> bool:
        """Try Greenhouse name-attr first, then label / aria-label / placeholder."""
        selectors = []
        gh_name = _GREENHOUSE_NAME_MAP.get(label_text)
        if gh_name:
            selectors.append(f'input[name="{gh_name}"]')
            selectors.append(f'textarea[name="{gh_name}"]')
        selectors.extend([
            f'label:has-text("{label_text}") input',
            f'label:has-text("{label_text}") >> input',
            f'input[aria-label="{label_text}"]',
            f'input[placeholder*="{label_text}" i]',
        ])

        for selector in selectors:
            try:
                el = page.locator(selector).first
                if el.is_visible(timeout=1000):
                    el.click()
                    el.fill(value)
                    logger.info(f"Filled '{label_text}' via {selector}")
                    return True
            except Exception:
                continue
        logger.debug(f"Could not find field for '{label_text}'")
        return False

    def _try_upload_resume(self, page: Page, resume_path: str) -> bool:
        """Upload resume via Greenhouse's file input."""
        selectors = [
            'input[type="file"][name="job_application[resume]"]',
            'input[type="file"][name*="resume" i]',
            'input[type="file"][accept*=".pdf"]',
            'input[type="file"]',
        ]
        for selector in selectors:
            try:
                file_input = page.locator(selector).first
                if file_input.count() > 0:
                    file_input.set_input_files(resume_path)
                    logger.info(f"Uploaded resume via {selector}")
                    return True
            except Exception:
                continue
        return False

    def _load_cover_letter(self, cover_letter_path: str) -> str:
        path = Path(cover_letter_path)
        if path.exists():
            return path.read_text(encoding="utf-8")
        if len(cover_letter_path) > 100:
            return cover_letter_path
        return ""

    def _try_paste_cover_letter(self, page: Page, text: str) -> bool:
        """Paste cover letter into Greenhouse's cover-letter textarea."""
        selectors = [
            'textarea[name="job_application[cover_letter]"]',
            'textarea[name*="cover" i]',
            'textarea[aria-label*="cover" i]',
            'textarea[placeholder*="cover" i]',
            "textarea",
        ]
        for selector in selectors:
            try:
                el = page.locator(selector).first
                if el.is_visible(timeout=1000):
                    el.click()
                    el.fill(text)
                    logger.info(f"Pasted cover letter via {selector}")
                    return True
            except Exception:
                continue
        return False
