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
from ._common import wait_for_form_ready
from .field_maps import run_field_map_fill

logger = logging.getLogger("prepare_dom.lever")

_CANONICAL_HOSTS = {"jobs.lever.co", "jobs.eu.lever.co"}
_FORM_SUFFIX = "/apply"


def _is_canonical_form_url(url: str) -> bool:
    """True when ``url`` already matches the canonical
    ``jobs(.eu)?.lever.co/<org>/<jobId>`` shape (optionally with the
    ``/apply`` form suffix already appended).

    Mirrors ``prepare_dom/ashby.py::_is_canonical_form_url`` — see that
    docstring for the embed-URL rationale. Lever forms can also be embedded
    on a company's own careers page; an embed URL won't have this host or
    path shape, so the ``/apply`` path-mutation hop is skipped for it and
    the frame-aware fill primitives take over instead.
    """
    parsed = urlparse(url or "")
    if (parsed.hostname or "").lower() not in _CANONICAL_HOSTS:
        return False
    path = parsed.path.rstrip("/")
    if path.endswith(_FORM_SUFFIX):
        path = path[: -len(_FORM_SUFFIX)]
    segments = [s for s in path.split("/") if s]
    return len(segments) == 2


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
            #
            # As with Ashby, that path-mutation only makes sense on a
            # canonical jobs(.eu)?.lever.co job-board URL. An embed (a
            # company's own careers page hosting the Lever form) has no org
            # slug to build a canonical target from, so skip the goto for
            # a non-canonical URL and rely on the frame-aware fill
            # primitives to find the form inside its iframe instead.
            current = page.url
            if _is_canonical_form_url(current):
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
            else:
                logger.info(
                    "lever: URL is not a canonical jobs(.eu)?.lever.co/"
                    "<org>/<jobId> path (likely a company-page embed) - "
                    "staying put and relying on frame-aware fill"
                )

            # Tolerant readiness wait (Task 3 / #4) — replaces the old
            # ``networkidle`` wait, which analytics-heavy pages routinely
            # never satisfy within 15s, silently degrading a healthy page
            # into an unnecessary hand-off. See ``_common.wait_for_form_ready``.
            readiness = wait_for_form_ready(page, log=logger)
            if not readiness["ready"]:
                logger.warning(
                    "lever: form-readiness check timed out - proceeding anyway"
                )
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
                readiness_timeout=not readiness["ready"],
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
