"""prepare_dom/greenhouse.py — Greenhouse ATS DOM-based form filler (M-3).

Greenhouse hosts forms at boards.greenhouse.io / job-boards.greenhouse.io
/ apply.greenhouse.io. The forms are server-rendered HTML with stable
``name`` attributes like ``job_application[first_name]``, so we prefer those
over label-based heuristics. Uses ``job["form_answers"]`` (M-1) as the
authoritative source of identity / contact / location values — zero
Anthropic API calls.

Greenhouse loads the form directly on the canonical job URL — no
overview-to-form navigation needed, unlike Ashby and Lever (PR-22). The
sibling ``prepare_dom/ashby.py`` appends ``/application`` and
``prepare_dom/lever.py`` appends ``/apply`` before surveying because their
URL spaces split overview vs. form. Greenhouse does not, so this adapter
operates directly on whatever URL the orchestrator hands it. If a future
Greenhouse template introduces a separate overview page, mirror the PR-22
URL-prepend pattern from those siblings.

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
``prepare_dom/_common.py``. Part A (#3) then moved the Greenhouse field
*definitions* — the canonical input ``name`` per label, the phone selector
chain, the resume/cover-letter selector lists — out of this file and into
``field_maps.yml`` under the ``greenhouse`` key. This adapter is now thin:
load the spec list, build the value map, call ``apply_field_map``. The
``BaseApplicant`` import is the explicit
``jobpipe.submit.adapters.applicant_base`` path.
"""

import logging
import time

from jobpipe.submit.adapters.applicant_base import BaseApplicant
from ._common import wait_for_form_ready
from .field_maps import run_field_map_fill

logger = logging.getLogger("prepare_dom.greenhouse")


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
        """Fill a Greenhouse application form from ``job["form_answers"]``.

        Greenhouse loads the form directly on the canonical job URL — no
        overview-to-form hop — so this adapter just waits for the page then
        runs the data-driven fill. Field definitions live in
        ``field_maps.yml`` under ``greenhouse``. Custom questions are NOT
        auto-filled (``note_custom_questions=True`` surfaces the operator
        note); they live in ``form_answers.additional_questions`` and the
        human pastes them from the cockpit.
        """
        try:
            # Tolerant readiness wait (Task 3 / #4) — replaces the old
            # ``networkidle`` wait, which analytics-heavy pages routinely
            # never satisfy within 15s, silently degrading a healthy page
            # into an unnecessary hand-off. See ``_common.wait_for_form_ready``.
            readiness = wait_for_form_ready(page, log=logger)
            if not readiness["ready"]:
                logger.warning(
                    "greenhouse: form-readiness check timed out - proceeding anyway"
                )
            time.sleep(1)

            return run_field_map_fill(
                self, page, job, "greenhouse",
                screenshot_label=f"greenhouse_{job.get('id', 'unknown')}",
                resume_path=resume_path,
                cover_letter_path=cover_letter_path,
                note_custom_questions=True,
                readiness_timeout=not readiness["ready"],
                log=logger,
            )

        except Exception as e:
            logger.error(f"Greenhouse form fill error: {e}")
            return {
                "success": False,
                "notes": f"Error during form fill: {e}",
                "fields_filled": [],
                "required_empty": [],
            }
