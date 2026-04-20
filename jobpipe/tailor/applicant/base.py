"""
applicant/base.py — Abstract base class for site-specific job applicants.

Each ATS platform (Greenhouse, Lever, Indeed, etc.) gets its own subclass
that implements the form-filling logic via Playwright.
"""

import logging
from abc import ABC, abstractmethod
from pathlib import Path
from datetime import datetime

from playwright.sync_api import sync_playwright, Page, Browser

from config import OUTPUT_DIR

logger = logging.getLogger("applicant")


class BaseApplicant(ABC):
    """
    Base class for automated job application submission.

    Subclasses must implement:
        - detect(url) -> bool: whether this applicant handles the given URL
        - fill_form(page, job, resume_path, cover_letter_path) -> dict
    """

    name: str = "base"

    def __init__(self):
        self.playwright = None
        self.browser = None

    def start_browser(self, headless: bool = True) -> Browser:
        """Launch a Playwright Chromium browser."""
        self.playwright = sync_playwright().start()
        self.browser = self.playwright.chromium.launch(headless=headless)
        logger.info(f"Browser launched (headless={headless})")
        return self.browser

    def stop_browser(self):
        """Close the browser and Playwright."""
        if self.browser:
            self.browser.close()
        if self.playwright:
            self.playwright.stop()
        logger.info("Browser closed")

    @staticmethod
    @abstractmethod
    def detect(url: str) -> bool:
        """Return True if this applicant can handle the given URL."""
        pass

    @abstractmethod
    def fill_form(self, page: Page, job: dict,
                  resume_path: str = None,
                  cover_letter_path: str = None) -> dict:
        """
        Fill out the application form on the given page.

        Args:
            page: Playwright page navigated to the application URL.
            job: Full job record from Supabase.
            resume_path: Path to the tailored resume PDF.
            cover_letter_path: Path to the cover letter file.

        Returns:
            Dict with:
                - success: bool
                - screenshot_path: str — path to screenshot of filled form
                - notes: str — description of what was filled
        """
        pass

    def take_screenshot(self, page: Page, label: str = "form") -> str:
        """Capture a screenshot of the current page state."""
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        path = OUTPUT_DIR / f"screenshot_{label}_{timestamp}.png"
        page.screenshot(path=str(path), full_page=False)
        logger.info(f"Screenshot saved: {path}")
        return str(path)

    def apply(self, job: dict, resume_path: str = None,
              cover_letter_path: str = None,
              headless: bool = True) -> dict:
        """
        Full application flow: open browser, navigate, fill form, screenshot.

        Does NOT submit — stops at the filled form for human review.

        Returns:
            Dict with form fill results + screenshot.
        """
        url = job.get("application_url") or job.get("url")
        if not url:
            return {"success": False, "notes": "No application URL available"}

        try:
            self.start_browser(headless=headless)
            page = self.browser.new_page()
            page.goto(url, wait_until="networkidle", timeout=30000)

            result = self.fill_form(page, job, resume_path, cover_letter_path)

            if result.get("success"):
                screenshot = self.take_screenshot(page, label=job.get("id", "unknown"))
                result["screenshot_path"] = screenshot

            return result

        except Exception as e:
            logger.error(f"Application failed for {job.get('company')}: {e}")
            return {"success": False, "notes": f"Error: {str(e)}"}

        finally:
            self.stop_browser()

    def submit(self, job: dict,
               resume_path: str = None,
               cover_letter_path: str = None,
               headless: bool = True) -> dict:
        """
        Resume the application and click submit.

        Called after human approval (submit_confirmed status).  Re-navigates
        and re-fills the form since browser sessions don't persist between
        runs, then clicks the ATS's submit button and waits for confirmation.

        Subclasses should override with platform-specific submit logic.

        Returns dict with:
            - success: bool
            - submitted: bool   (True only if confirmation detected)
            - screenshot_path: str | None
            - notes: str
        """
        raise NotImplementedError(
            f"{self.name} applicant does not yet support automated submission. "
            "Mark as applied manually."
        )
