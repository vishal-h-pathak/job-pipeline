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
``prepare_dom/_common.py``. Part A (#3) then moved the Lever field
*definitions* — the canonical ``name`` per label, the phone selector chain,
the resume/cover-letter selector lists — into ``field_maps.yml`` under the
``lever`` key. This adapter keeps only the Lever-specific *behaviour*: the
overview->``/apply`` URL hop and the full-name override on the Name / Full
Name value keys (Lever wants the whole name in one ``name="name"`` field).
The ``BaseApplicant`` import is the explicit
``jobpipe.submit.adapters.applicant_base`` path.
"""

import logging
import time
from urllib.parse import urlparse, urlunparse

from jobpipe.submit.adapters.applicant_base import BaseApplicant
from .field_maps import run_field_map_fill

logger = logging.getLogger("prepare_dom.lever")


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
        try:
            # Lever URLs from the hunt are typically the overview page
            # (jobs.lever.co/{org}/{job_id}); the application form lives
            # at /{org}/{job_id}/apply. The form selectors (name="resume",
            # name="comments", name="phone") only exist on /apply, so
            # without this hop fill_form would survey an empty page.
            # Idempotent — if the URL already ends in /apply, no extra goto.
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

            # Lever wants the full name in a single field. Override the Name /
            # Full Name value keys to the computed full name before the
            # data-driven fill (the lever field map points all three name
            # specs at name="name").
            fa = job.get("form_answers") or {}
            full_name = fa.get("full_name") or (
                f"{fa.get('first_name', '')} {fa.get('last_name', '')}".strip()
            )

            return run_field_map_fill(
                self, page, job, "lever",
                screenshot_label=f"lever_{job.get('id', 'unknown')}",
                resume_path=resume_path,
                cover_letter_path=cover_letter_path,
                value_overrides={"Name": full_name, "Full Name": full_name},
                note_custom_questions=True,
                log=logger,
            )

        except Exception as e:
            logger.error(f"Lever form fill error: {e}")
            return {
                "success": False,
                "notes": f"Error during form fill: {e}",
                "fields_filled": [],
                "required_empty": [],
            }
