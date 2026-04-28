"""applicant/lever.py — Lever ATS DOM-based form filler (M-3).

Lever hosts forms at jobs.lever.co/<org>/<job_id>/apply (US) and
jobs.eu.lever.co/<org>/<job_id>/apply (EU). The standard fields use
simple `name="name"`, `name="email"`, `name="phone"` attributes. URL
fields (LinkedIn, GitHub, etc.) use `name="urls[LinkedIn]"` patterns.
Reads `job["form_answers"]` (M-1) for all values — zero Anthropic API
calls.

Same shape as `applicant/ashby.py`: static `detect()`, `fill_form()`
returning `{success, screenshot_path, notes, fields_filled}`. Does NOT
click Submit. After M-5 the orchestrator screenshots, marks the row
`awaiting_human_submit`, and blocks on terminal `input()` while the
human reviews the visible browser.

Known M-3 limitation: Lever's per-card custom questions
(`name="cards[<uuid>][field0]"` patterns) are NOT auto-filled here —
the human pastes draft answers from `form_answers.additional_questions`
via the cockpit copy buttons.
"""

import logging
import time
from pathlib import Path

from playwright.sync_api import Page

from applicant.ashby import _build_field_map
from applicant.base import BaseApplicant

logger = logging.getLogger("applicant.lever")


# Lever uses flat name attrs for standard fields and urls[Service] for
# social URLs. Map our label keys to those.
_LEVER_NAME_MAP = {
    "Full Name": "name",
    "Name": "name",
    "First Name": "name",
    "Email": "email",
    "Phone": "phone",
    "Current Company": "org",
    "Company": "org",
    "Current Title": "title",
    "Title": "title",
    "Location": "location",
    "Current Location": "location",
    "City": "location",
    "LinkedIn URL": "urls[LinkedIn]",
    "LinkedIn": "urls[LinkedIn]",
    "GitHub URL": "urls[GitHub]",
    "GitHub": "urls[GitHub]",
    "Portfolio": "urls[Portfolio]",
    "Website": "urls[Other]",
}


class LeverApplicant(BaseApplicant):
    """Playwright-based DOM form filler for Lever ATS applications."""

    name: str = "lever"

    # ── Detection ────────────────────────────────────────────────────────────

    @staticmethod
    def detect(url: str) -> bool:
        """Return True for Lever-hosted application URLs."""
        url_lower = (url or "").lower()
        return (
            "jobs.lever.co" in url_lower
            or "jobs.eu.lever.co" in url_lower
        )

    # ── Form filling ─────────────────────────────────────────────────────────

    def fill_form(
        self,
        page: Page,
        job: dict,
        resume_path: str = None,
        cover_letter_path: str = None,
    ) -> dict:
        """Fill a Lever application form from `job["form_answers"]`."""
        filled = []
        notes_parts = []

        try:
            page.wait_for_load_state("networkidle", timeout=15000)
            time.sleep(1)

            # Lever wants the full name in a single field. Build a tweaked
            # field_map: prefer full_name for the "Name" key.
            field_map = _build_field_map(job)
            fa = job.get("form_answers") or {}
            full_name = fa.get("full_name") or (
                f"{fa.get('first_name', '')} {fa.get('last_name', '')}".strip()
            )
            field_map["Name"] = full_name
            field_map["Full Name"] = full_name

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

            # Cover letter — Lever uses `<textarea name="comments">`
            if cover_letter_path:
                cover_text = self._load_cover_letter(cover_letter_path)
                if cover_text and self._try_paste_cover_letter(page, cover_text):
                    notes_parts.append("Pasted cover letter")
                elif cover_text:
                    notes_parts.append("Cover letter: no textarea found")

            # Custom questions: not auto-filled (see module docstring).
            qs = (job.get("form_answers") or {}).get("additional_questions") or []
            if qs:
                notes_parts.append(
                    f"{len(qs)} role-specific question(s) NOT auto-filled - "
                    f"paste from cockpit drafts"
                )

            screenshot_path = self.take_screenshot(
                page, label=f"lever_{job.get('id', 'unknown')}"
            )
            notes_parts.append(f"Screenshot: {screenshot_path}")

            return {
                "success": len(filled) > 0,
                "screenshot_path": screenshot_path,
                "notes": "\n".join(notes_parts),
                "fields_filled": filled,
            }

        except Exception as e:
            logger.error(f"Lever form fill error: {e}")
            return {
                "success": False,
                "notes": (
                    f"Error during form fill: {e}\n"
                    f"Partial: {', '.join(notes_parts)}"
                ),
            }

    # ── Private helpers ──────────────────────────────────────────────────────

    def _try_fill_field(self, page: Page, label_text: str, value: str) -> bool:
        """Try Lever name-attr first, then label / aria-label / placeholder."""
        selectors = []
        lever_name = _LEVER_NAME_MAP.get(label_text)
        if lever_name:
            selectors.append(f'input[name="{lever_name}"]')
            selectors.append(f'textarea[name="{lever_name}"]')
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
        """Upload resume via Lever's file input."""
        selectors = [
            'input[type="file"][name="resume"]',
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
        """Paste cover letter into Lever's `comments` textarea."""
        selectors = [
            'textarea[name="comments"]',
            'textarea[name*="cover" i]',
            'textarea[aria-label*="cover" i]',
            'textarea[placeholder*="cover" i]',
            'textarea[placeholder*="why" i]',
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
