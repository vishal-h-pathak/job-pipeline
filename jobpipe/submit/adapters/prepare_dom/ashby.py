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
live in ``prepare_dom/_common.py``. This file keeps only the Ashby-specific
specialization — the SPA-hydration networkidle wait, the extra fuzzy
``input[name*="..."]`` fallbacks that Lever and Greenhouse don't need, the
full-name vs first/last branch behavior, and the union of cover-letter
textarea selectors that Ashby requires (including the
``div[contenteditable="true"]`` fallback for rich-text fields). The
``BaseApplicant`` import switched from the bare ``from applicant.base`` to
the explicit ``jobpipe.submit.adapters.applicant_base`` path, so this module
no longer depends on the legacy tailor sys.path bootstrap.
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
    paste_textarea,
    upload_file,
)

logger = logging.getLogger("prepare_dom.ashby")


# Ashby cover-letter textareas vary across boards — try the most specific
# selectors first (label-attr matches), then fall back to any textarea, then
# rich-text contenteditable as a last resort.
_ASHBY_COVER_LETTER_SELECTORS = [
    'textarea[name*="cover" i]',
    'textarea[aria-label*="cover" i]',
    'textarea[placeholder*="cover" i]',
    'textarea[name*="additional" i]',
    'textarea[aria-label*="additional" i]',
    "textarea",
    'div[contenteditable="true"]',
]

# Ashby resume input has no canonical name attr — accept any file input,
# preferring PDF-typed slots first.
_ASHBY_RESUME_SELECTORS = [
    'input[type="file"]',
    'input[accept*=".pdf"]',
    'input[accept*="application/pdf"]',
]

# Phone selector chain for Ashby. Same intl-tel-input coverage motive as
# Greenhouse — see prepare_dom/greenhouse.py::_GREENHOUSE_PHONE_SELECTORS.
# Ashby has no Phone entry in a name-map (the adapter falls back to the
# fuzzy ``input[name*="phone"]`` matcher in ``_ashby_field_selectors``),
# so the per-form fallback list is the generic ``id`` / ``aria-label``
# anchors. ``input[type="tel"]:visible`` still leads — intl-tel-input's
# DOM pattern is library-defined and identical across host ATSes.
_ASHBY_PHONE_SELECTORS = [
    'input[type="tel"]:visible',
    'input[id="phone"]',
    'input[aria-label="Phone"]',
]


def _ashby_field_selectors(label_text: str) -> list[str]:
    """Ashby falls back to fuzzy ``input[name*="..."]`` matches when the
    label-based selectors all miss. Lever and Greenhouse don't need this
    extra layer because they have explicit per-label name maps."""
    return label_selectors(label_text) + [
        f'input[name*="{label_text.lower().replace(" ", "_")}"]',
        f'input[name*="{label_text.lower().replace(" ", "")}"]',
    ]


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
        We try multiple selector strategies per field and stop at the first
        match.
        """
        filled = []
        notes_parts = []

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

            field_map = build_field_map(job)
            for label_text, value in field_map.items():
                if not value:
                    continue
                if label_text == "Phone":
                    selectors = (
                        _ASHBY_PHONE_SELECTORS
                        + _ashby_field_selectors(label_text)
                    )
                else:
                    selectors = _ashby_field_selectors(label_text)
                if fill_text(page, selectors, value, log=logger):
                    filled.append(label_text)

            notes_parts.append(
                f"Filled fields: {', '.join(filled) if filled else 'none'}"
            )

            # Resume upload
            if resume_path and Path(resume_path).exists():
                if upload_file(page, _ASHBY_RESUME_SELECTORS, resume_path, log=logger):
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
                    page, _ASHBY_COVER_LETTER_SELECTORS, cover_text, log=logger
                ):
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


# ── Backward-compat alias ─────────────────────────────────────────────────
#
# ``_build_field_map`` was a private helper in this file before PR-7 and the
# tailor-side shim at ``jobpipe/tailor/applicant/ashby.py`` re-exports it
# alongside ``AshbyApplicant``. The shim is part of the PR-9 cleanup; until
# then keep the alias here so the shim's re-export keeps resolving without
# the shim having to know about the ``_common`` move.
_build_field_map = build_field_map
