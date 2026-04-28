"""applicant/ashby.py — Ashby ATS (ashbyhq.com) DOM-based form filler (M-3).

Navigates to an Ashby-hosted application page, fills standard fields by
reading values from `job["form_answers"]` (the structured JSON written
by the M-1 tailoring step), uploads a resume PDF, pastes a cover letter,
takes a screenshot, and returns. Zero Anthropic API calls — pure
Playwright + DOM selectors.

The handler does NOT click Submit. After M-3 + M-5, the orchestrator
takes the post-fill screenshot, marks the row `awaiting_human_submit`,
and blocks on a terminal `input()` while the human reviews the visible
browser, fixes anything wrong, clicks Submit themselves, and then comes
back to the dashboard cockpit to click "Mark Applied".
"""

import logging
import time
from pathlib import Path

from playwright.sync_api import Page

from applicant.base import BaseApplicant

logger = logging.getLogger("applicant.ashby")


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
        page: Page,
        job: dict,
        resume_path: str = None,
        cover_letter_path: str = None,
    ) -> dict:
        """Fill an Ashby application form from `job["form_answers"]`.

        Ashby renders inputs inside a React app. Most labels are explicit
        `<label>` elements; some use `aria-label`; some use placeholders.
        We try multiple selector strategies per field and stop at the
        first match.
        """
        filled = []
        notes_parts = []

        try:
            page.wait_for_load_state("networkidle", timeout=15000)
            time.sleep(2)  # extra buffer for React hydration

            field_map = _build_field_map(job)
            for label_text, value in field_map.items():
                if not value:
                    continue
                if self._try_fill_by_label(page, label_text, value):
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

            screenshot_path = self.take_screenshot(
                page, label=f"ashby_{job.get('id', 'unknown')}"
            )
            notes_parts.append(f"Screenshot: {screenshot_path}")

            return {
                "success": len(filled) > 0,
                "screenshot_path": screenshot_path,
                "notes": "\n".join(notes_parts),
                "fields_filled": filled,
            }

        except Exception as e:
            logger.error(f"Ashby form fill error: {e}")
            return {
                "success": False,
                "notes": (
                    f"Error during form fill: {e}\n"
                    f"Partial: {', '.join(notes_parts)}"
                ),
            }

    # ── Private helpers ──────────────────────────────────────────────────────

    def _try_fill_by_label(self, page: Page, label_text: str, value: str) -> bool:
        """Find an input by label / aria-label / placeholder / name and fill it."""
        selectors = [
            f'label:has-text("{label_text}") input',
            f'label:has-text("{label_text}") >> input',
            f'input[aria-label="{label_text}"]',
            f'input[placeholder*="{label_text}" i]',
            f'input[name*="{label_text.lower().replace(" ", "_")}"]',
            f'input[name*="{label_text.lower().replace(" ", "")}"]',
        ]
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
        """Upload resume via file input."""
        selectors = [
            'input[type="file"]',
            'input[accept*=".pdf"]',
            'input[accept*="application/pdf"]',
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
        """Load cover letter text from file or treat the arg as raw text."""
        path = Path(cover_letter_path)
        if path.exists():
            return path.read_text(encoding="utf-8")
        if len(cover_letter_path) > 100:
            return cover_letter_path
        return ""

    def _try_paste_cover_letter(self, page: Page, text: str) -> bool:
        """Paste cover letter into a textarea or contenteditable field."""
        selectors = [
            'textarea[name*="cover" i]',
            'textarea[aria-label*="cover" i]',
            'textarea[placeholder*="cover" i]',
            'textarea[name*="additional" i]',
            'textarea[aria-label*="additional" i]',
            "textarea",
            'div[contenteditable="true"]',
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


# ── Shared field-map builder (reused by greenhouse.py / lever.py too) ──────

def _build_field_map(job: dict) -> dict[str, str]:
    """Build a label-keyed dict of values from `job["form_answers"]`.

    Identity / contact / location / comp values come from the M-1
    form_answers JSON (which itself was filled from profile.yml in
    Python — never LLM-generated). Returning a label-keyed dict lets
    each per-ATS handler reuse the same source while keeping its own
    selector strategy.
    """
    fa = job.get("form_answers") or {}
    return {
        "First Name": fa.get("first_name") or "",
        "Last Name": fa.get("last_name") or "",
        "Full Name": fa.get("full_name") or "",
        "Name": fa.get("full_name") or "",
        "Email": fa.get("email") or "",
        "Phone": fa.get("phone") or "",
        "LinkedIn URL": fa.get("linkedin_url") or "",
        "LinkedIn": fa.get("linkedin_url") or "",
        "GitHub URL": fa.get("github_url") or "",
        "GitHub": fa.get("github_url") or "",
        "Portfolio": fa.get("portfolio_url") or "",
        "Website": fa.get("portfolio_url") or "",
        "Location": fa.get("current_location") or "",
        "Current Location": fa.get("current_location") or "",
        "City": fa.get("current_location") or "",
        "Current Company": fa.get("current_company") or "",
        "Company": fa.get("current_company") or "",
        "Current Title": fa.get("current_title") or "",
        "Title": fa.get("current_title") or "",
    }
