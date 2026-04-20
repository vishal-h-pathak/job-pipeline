"""
applicant/ashby.py — Ashby ATS (ashbyhq.com) form filler.

Navigates to an Ashby-hosted application page, fills in standard fields
(name, email, phone, LinkedIn, location), uploads a resume PDF, pastes
a cover letter, and takes a screenshot.  Does NOT click submit.
"""

import logging
import time
from pathlib import Path

from playwright.sync_api import Page

from applicant.base import BaseApplicant

logger = logging.getLogger("applicant.ashby")


class AshbyApplicant(BaseApplicant):
    """Playwright-based form filler for Ashby ATS applications."""

    name: str = "ashby"

    # ── Detection ────────────────────────────────────────────────────────────

    @staticmethod
    def detect(url: str) -> bool:
        """Return True if the URL points to an Ashby-hosted application."""
        url_lower = url.lower()
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
        """
        Fill out an Ashby application form.

        Ashby forms typically render labeled input fields inside a React app.
        Common selectors:
            - Input fields with name/aria-label attributes
            - File upload inputs for resume
            - Textarea or rich-text fields for cover letter / additional info
        """
        filled = []
        notes_parts = []

        try:
            # Give the SPA time to render
            page.wait_for_load_state("networkidle", timeout=15000)
            time.sleep(2)  # extra buffer for React hydration

            # ── Candidate profile info ───────────────────────────────────
            # Ashby forms usually have these fields; we try multiple selector
            # strategies since the exact markup varies by employer config.

            field_map = {
                "First Name": job.get("candidate_first_name", "Vishal"),
                "Last Name": job.get("candidate_last_name", "Pathak"),
                "Email": job.get("candidate_email", "vshlpthk1@gmail.com"),
                "Phone": job.get("candidate_phone", ""),
                "LinkedIn URL": job.get("candidate_linkedin", "https://linkedin.com/in/vishalhpathak"),
                "LinkedIn": job.get("candidate_linkedin", "https://linkedin.com/in/vishalhpathak"),
                "Location": job.get("candidate_location", "Atlanta, GA"),
                "Current Location": job.get("candidate_location", "Atlanta, GA"),
                "Current Company": job.get("candidate_company", "Georgia Tech Research Institute"),
                "Current Title": job.get("candidate_title", "Algorithms & Analysis Engineer"),
            }

            for label_text, value in field_map.items():
                if not value:
                    continue
                if self._try_fill_by_label(page, label_text, value):
                    filled.append(label_text)

            notes_parts.append(f"Filled fields: {', '.join(filled) if filled else 'none'}")

            # ── Resume upload ────────────────────────────────────────────
            if resume_path and Path(resume_path).exists():
                uploaded = self._try_upload_resume(page, resume_path)
                if uploaded:
                    notes_parts.append(f"Uploaded resume: {Path(resume_path).name}")
                else:
                    notes_parts.append("Resume upload: could not find file input")
            elif resume_path:
                notes_parts.append(f"Resume path not found: {resume_path}")

            # ── Cover letter ─────────────────────────────────────────────
            if cover_letter_path:
                cover_text = self._load_cover_letter(cover_letter_path)
                if cover_text:
                    pasted = self._try_paste_cover_letter(page, cover_text)
                    if pasted:
                        notes_parts.append("Pasted cover letter into long-text field")
                    else:
                        notes_parts.append("Cover letter: could not find textarea")

            # ── Screenshot ───────────────────────────────────────────────
            screenshot_path = self.take_screenshot(page, label=f"ashby_{job.get('id', 'unknown')}")
            notes_parts.append(f"Screenshot: {screenshot_path}")

            success = len(filled) > 0
            return {
                "success": success,
                "screenshot_path": screenshot_path,
                "notes": "\n".join(notes_parts),
                "fields_filled": filled,
            }

        except Exception as e:
            logger.error(f"Ashby form fill error: {e}")
            return {
                "success": False,
                "notes": f"Error during form fill: {str(e)}\nPartial: {', '.join(notes_parts)}",
            }

    # ── Private helpers ──────────────────────────────────────────────────────

    def _try_fill_by_label(self, page: Page, label_text: str, value: str) -> bool:
        """
        Try to find an input field by its label text and fill it.

        Ashby uses several patterns:
          1. <label>Label</label> with a for= pointing to <input id=...>
          2. <label> wrapping an <input>
          3. <input placeholder="Label">
          4. <input aria-label="Label">
        """
        selectors = [
            # Exact label association
            f'label:has-text("{label_text}") input',
            f'label:has-text("{label_text}") >> input',
            # aria-label on the input itself
            f'input[aria-label="{label_text}"]',
            # Placeholder match
            f'input[placeholder*="{label_text}" i]',
            # data-testid patterns Ashby sometimes uses
            f'input[name*="{label_text.lower().replace(" ", "_")}"]',
            f'input[name*="{label_text.lower().replace(" ", "")}"]',
        ]

        for selector in selectors:
            try:
                el = page.locator(selector).first
                if el.is_visible(timeout=1000):
                    el.click()
                    el.fill(value)
                    logger.info(f"Filled '{label_text}' via selector: {selector}")
                    return True
            except Exception:
                continue

        logger.debug(f"Could not find field for '{label_text}'")
        return False

    def _try_upload_resume(self, page: Page, resume_path: str) -> bool:
        """Try to upload a resume via file input."""
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

        # Some Ashby forms use a drag-drop zone with a hidden input
        try:
            file_inputs = page.locator('input[type="file"]')
            if file_inputs.count() > 0:
                file_inputs.first.set_input_files(resume_path)
                logger.info("Uploaded resume via generic file input")
                return True
        except Exception:
            pass

        return False

    def _load_cover_letter(self, cover_letter_path: str) -> str:
        """Load cover letter text from file or treat as raw text."""
        path = Path(cover_letter_path)
        if path.exists():
            return path.read_text(encoding="utf-8")
        # If the "path" is actually the cover letter text itself
        if len(cover_letter_path) > 100:
            return cover_letter_path
        return ""

    def _try_paste_cover_letter(self, page: Page, text: str) -> bool:
        """Try to paste cover letter into a textarea or contenteditable field."""
        selectors = [
            'textarea[name*="cover" i]',
            'textarea[aria-label*="cover" i]',
            'textarea[placeholder*="cover" i]',
            'textarea[name*="additional" i]',
            'textarea[aria-label*="additional" i]',
            # Generic textarea (last resort — the form might only have one)
            "textarea",
            # Contenteditable divs (rich text editors)
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

    # ── Submission ───────────────────────────────────────────────────────────

    def submit(
        self,
        job: dict,
        resume_path: str = None,
        cover_letter_path: str = None,
        headless: bool = True,
    ) -> dict:
        """
        Re-open the Ashby application, re-fill the form, and click Submit.

        Called after the user has confirmed `ready_to_submit` → `submit_confirmed`
        in the dashboard. Browser sessions don't persist across runs, so we have
        to repeat the fill before clicking submit.

        Returns dict with:
            - success: bool
            - submitted: bool  (True only if confirmation page detected)
            - screenshot_path: str  (post-submit screenshot)
            - notes: str
        """
        url = job.get("application_url") or job.get("url")
        if not url:
            return {"success": False, "submitted": False, "notes": "No application URL"}

        notes_parts = []
        post_screenshot = None

        try:
            self.start_browser(headless=headless)
            page = self.browser.new_page()
            page.goto(url, wait_until="networkidle", timeout=30000)

            # Re-fill the form using the existing fill logic.
            fill_result = self.fill_form(
                page,
                job,
                resume_path=resume_path,
                cover_letter_path=cover_letter_path,
            )
            notes_parts.append(f"Re-fill: {fill_result.get('notes', 'unknown')}")

            if not fill_result.get("success"):
                post_screenshot = self.take_screenshot(
                    page, label=f"ashby_submit_fail_{job.get('id', 'unknown')}"
                )
                return {
                    "success": False,
                    "submitted": False,
                    "screenshot_path": post_screenshot,
                    "notes": "Re-fill failed prior to submit.\n" + "\n".join(notes_parts),
                }

            # Small buffer so the form's internal state settles before submit.
            time.sleep(1)

            # Click the submit button. Ashby's submit is typically:
            #   <button type="submit">Submit Application</button>
            # We try a few variations, preferring the most specific.
            clicked = self._click_submit(page)
            if not clicked:
                post_screenshot = self.take_screenshot(
                    page, label=f"ashby_no_submit_btn_{job.get('id', 'unknown')}"
                )
                return {
                    "success": False,
                    "submitted": False,
                    "screenshot_path": post_screenshot,
                    "notes": "Could not locate submit button.\n" + "\n".join(notes_parts),
                }

            notes_parts.append("Clicked submit.")

            # Wait for a confirmation state. Ashby renders a success/confirmation
            # section after a successful submit. We look for common signals.
            confirmed = self._wait_for_confirmation(page)
            post_screenshot = self.take_screenshot(
                page, label=f"ashby_post_submit_{job.get('id', 'unknown')}"
            )

            if confirmed:
                notes_parts.append("Confirmation detected.")
                return {
                    "success": True,
                    "submitted": True,
                    "screenshot_path": post_screenshot,
                    "notes": "\n".join(notes_parts),
                }
            else:
                notes_parts.append(
                    "No confirmation text detected within timeout — "
                    "submission may have failed or requires manual verification."
                )
                return {
                    "success": False,
                    "submitted": False,
                    "screenshot_path": post_screenshot,
                    "notes": "\n".join(notes_parts),
                }

        except Exception as e:
            logger.error(f"Ashby submit error: {e}")
            return {
                "success": False,
                "submitted": False,
                "screenshot_path": post_screenshot,
                "notes": f"Error during submit: {str(e)}\nPartial: " + "\n".join(notes_parts),
            }

        finally:
            self.stop_browser()

    def _click_submit(self, page: Page) -> bool:
        """Locate and click Ashby's submit button."""
        selectors = [
            'button[type="submit"]:has-text("Submit Application")',
            'button:has-text("Submit Application")',
            'button[type="submit"]:has-text("Submit")',
            'button:has-text("Submit")',
            'button[type="submit"]',
        ]
        for selector in selectors:
            try:
                btn = page.locator(selector).first
                if btn.is_visible(timeout=1500):
                    # Scroll into view first, then click.
                    btn.scroll_into_view_if_needed(timeout=2000)
                    btn.click()
                    logger.info(f"Clicked submit via {selector}")
                    return True
            except Exception:
                continue
        return False

    def _wait_for_confirmation(self, page: Page, timeout_ms: int = 15000) -> bool:
        """
        Wait for post-submit confirmation signals.

        Ashby typically replaces the form with a confirmation block containing
        text like "Application submitted" / "Thanks for applying" / "We've
        received your application". We poll for any of these up to timeout_ms.
        """
        confirmation_texts = [
            "application has been submitted",
            "application submitted",
            "we've received your application",
            "we have received your application",
            "thanks for applying",
            "thank you for applying",
            "thank you for your application",
            "you've applied",
            "successfully submitted",
        ]
        deadline = time.time() + (timeout_ms / 1000)
        while time.time() < deadline:
            try:
                # Prefer a URL change (Ashby sometimes redirects after submit).
                if "confirmation" in page.url.lower() or "submitted" in page.url.lower():
                    return True
                body_text = page.inner_text("body", timeout=2000).lower()
                if any(t in body_text for t in confirmation_texts):
                    return True
            except Exception:
                pass
            time.sleep(1)
        return False
