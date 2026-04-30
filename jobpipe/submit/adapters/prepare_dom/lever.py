"""prepare_dom/lever.py — Lever ATS DOM-based form filler (M-3).

Lever hosts forms at jobs.lever.co/<org>/<job_id>/apply (US) and
jobs.eu.lever.co/<org>/<job_id>/apply (EU). The standard fields use simple
``name="name"``, ``name="email"``, ``name="phone"`` attributes. URL fields
(LinkedIn, GitHub, etc.) use ``name="urls[LinkedIn]"`` patterns. Reads
``job["form_answers"]`` (M-1) for all values — zero Anthropic API calls.

Same shape as ``prepare_dom/ashby.py``: static ``detect()``, ``fill_form()``
returning ``{success, screenshot_path, notes, fields_filled}``. Does NOT
click Submit. After M-5 the orchestrator screenshots, marks the row
``awaiting_human_submit``, and blocks on terminal ``input()`` while the
human reviews the visible browser.

Known M-3 limitation: Lever's per-card custom questions
(``name="cards[<uuid>][field0]"`` patterns) are NOT auto-filled here — the
human pastes draft answers from ``form_answers.additional_questions`` via
the cockpit copy buttons. The PR-7 helper
``_common.note_unfilled_custom_questions`` surfaces the "N role-specific
question(s) NOT auto-filled" note to the operator.

PR-7 history: shared sync Playwright helpers moved to
``prepare_dom/_common.py``. This file keeps Lever-specific knowledge: the
``_LEVER_NAME_MAP`` (canonical input ``name`` per label), the full-name
override on the Name keys, and the Lever-specific resume/cover-letter
selector lists. The ``BaseApplicant`` import is now the explicit
``jobpipe.submit.adapters.applicant_base`` path.
"""

import logging
import time
from pathlib import Path
from urllib.parse import urlparse, urlunparse

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

logger = logging.getLogger("prepare_dom.lever")


# Lever uses flat name attrs for standard fields and urls[Service] for social
# URLs. Map our label keys to those.
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

# Phone selector chain for Lever. Same intl-tel-input coverage motive as
# Greenhouse — see prepare_dom/greenhouse.py::_GREENHOUSE_PHONE_SELECTORS.
# intl-tel-input is a generic JS library; its DOM pattern is identical
# regardless of host ATS, so the same ``type="tel":visible``-first
# strategy applies. The fallback chain uses Lever's canonical
# ``name="phone"`` (per ``_LEVER_NAME_MAP``) plus the generic ``id`` /
# ``aria-label`` anchors.
_LEVER_PHONE_SELECTORS = [
    'input[type="tel"]:visible',
    'input[name="phone"]',
    'input[id="phone"]',
    'input[aria-label="Phone"]',
]

_LEVER_RESUME_SELECTORS = [
    'input[type="file"][name="resume"]',
    'input[type="file"][name*="resume" i]',
    'input[type="file"][accept*=".pdf"]',
    'input[type="file"]',
]

_LEVER_COVER_LETTER_SELECTORS = [
    'textarea[name="comments"]',
    'textarea[name*="cover" i]',
    'textarea[aria-label*="cover" i]',
    'textarea[placeholder*="cover" i]',
    'textarea[placeholder*="why" i]',
    "textarea",
]


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
        page,
        job: dict,
        resume_path: str = None,
        cover_letter_path: str = None,
    ) -> dict:
        """Fill a Lever application form from ``job["form_answers"]``."""
        filled = []
        notes_parts = []

        try:
            # Lever URLs from the hunt are typically the overview page
            # (jobs.lever.co/{org}/{job_id}); the application form lives
            # at /{org}/{job_id}/apply. Lever-specific selectors below
            # (name="resume", name="comments", name="phone") only exist
            # on /apply, so without this hop fill_form would survey an
            # empty page and return success=False. Idempotent — if the
            # URL already ends in /apply, no extra goto.
            current = page.url
            parsed = urlparse(current)
            path = parsed.path.rstrip("/")
            if not path.endswith("/apply"):
                new_path = path + "/apply"
                target = urlunparse(parsed._replace(path=new_path))
                logger.info(
                    f"lever: navigating from overview to form: {target}"
                )
                page.goto(
                    target, wait_until="domcontentloaded", timeout=45000
                )

            page.wait_for_load_state("networkidle", timeout=15000)
            time.sleep(1)

            # Lever wants the full name in a single field. Build a tweaked
            # field_map: prefer full_name for the "Name" key.
            field_map = build_field_map(job)
            fa = job.get("form_answers") or {}
            full_name = fa.get("full_name") or (
                f"{fa.get('first_name', '')} {fa.get('last_name', '')}".strip()
            )
            field_map["Name"] = full_name
            field_map["Full Name"] = full_name

            for label_text, value in field_map.items():
                if not value:
                    continue
                if label_text == "Phone":
                    selectors = (
                        _LEVER_PHONE_SELECTORS
                        + label_selectors(label_text)
                    )
                else:
                    selectors = (
                        name_attr_selectors(_LEVER_NAME_MAP, label_text)
                        + label_selectors(label_text)
                    )
                if fill_text(page, selectors, value, log=logger):
                    filled.append(label_text)

            notes_parts.append(
                f"Filled fields: {', '.join(filled) if filled else 'none'}"
            )

            # Resume upload
            if resume_path and Path(resume_path).exists():
                if upload_file(page, _LEVER_RESUME_SELECTORS, resume_path, log=logger):
                    notes_parts.append(
                        f"Uploaded resume: {Path(resume_path).name}"
                    )
                else:
                    notes_parts.append("Resume upload: no file input found")
            elif resume_path:
                notes_parts.append(f"Resume path not found: {resume_path}")

            # Cover letter — Lever uses ``<textarea name="comments">``
            if cover_letter_path:
                cover_text = load_cover_letter(cover_letter_path)
                if cover_text and paste_textarea(
                    page, _LEVER_COVER_LETTER_SELECTORS, cover_text, log=logger
                ):
                    notes_parts.append("Pasted cover letter")
                elif cover_text:
                    notes_parts.append("Cover letter: no textarea found")

            # Custom questions: not auto-filled (see module docstring).
            note_unfilled_custom_questions(job, notes_parts)

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
