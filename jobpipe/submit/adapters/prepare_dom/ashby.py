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
live in ``prepare_dom/_common.py``. Part A (#3) then moved the Ashby field
*definitions* into ``field_maps.yml`` under the ``ashby`` key — including the
fuzzy ``input[name*="..."]`` fallbacks (via the per-ATS
``defaults: {fuzzy_name_fallback: true}`` block, since Ashby has no canonical
name map) and the union of cover-letter textarea selectors (with the
``div[contenteditable="true"]`` rich-text fallback). This adapter keeps only
the Ashby-specific *behaviour*: the overview->``/application`` URL hop and the
longer SPA-hydration wait. The ``BaseApplicant`` import is the explicit
``jobpipe.submit.adapters.applicant_base`` path.
"""

import logging
import time
from urllib.parse import urlparse, urlunparse

from jobpipe.submit.adapters.applicant_base import BaseApplicant
from ._common import wait_for_form_ready
from .field_maps import run_field_map_fill

logger = logging.getLogger("prepare_dom.ashby")

_CANONICAL_HOST = "jobs.ashbyhq.com"
_FORM_SUFFIX = "/application"


def _is_canonical_form_url(url: str) -> bool:
    """True when ``url`` already matches the canonical
    ``jobs.ashbyhq.com/<org>/<jobId>`` shape (optionally with the
    ``/application`` form suffix already appended).

    ``AshbyApplicant.detect`` matches on three signals: the
    ``jobs.ashbyhq.com`` host, the loose ``jobs.ashby`` substring, and the
    ``ashby_jid`` query param a company's OWN careers page uses to embed an
    Ashby form (no org slug anywhere in that URL — there is no reliable way
    to derive the canonical job-board URL from it). Appending
    ``/application`` to an embed's path just produces a 404 on the
    company's own site. This check is what lets ``fill_form`` tell the two
    apart: only a URL that is already canonical-shaped gets the
    ``/application`` path-mutation hop; anything else (an embed) is left
    alone and handled by the frame-aware fill primitives instead (the
    embedded form typically lives inside an iframe on the company's page).
    """
    parsed = urlparse(url or "")
    if (parsed.hostname or "").lower() != _CANONICAL_HOST:
        return False
    path = parsed.path.rstrip("/")
    if path.endswith(_FORM_SUFFIX):
        path = path[: -len(_FORM_SUFFIX)]
    segments = [s for s in path.split("/") if s]
    return len(segments) == 2


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
        The ``ashby`` field map tries multiple selector strategies per field
        (label/aria/placeholder + the fuzzy ``input[name*=...]`` fallback) and
        stops at the first match. Unlike Greenhouse / Lever, Ashby does NOT
        emit the custom-questions note (parity with the pre-rewrite adapter).
        """
        try:
            # Ashby URLs from the hunt are typically the overview page
            # (jobs.ashbyhq.com/{org}/{job_id}); the application form lives
            # at /{org}/{job_id}/application. Without this hop the surveyor
            # finds an empty page and returns success=False. Idempotent —
            # if the URL already ends in /application, no extra goto.
            #
            # But that path-mutation only makes sense on a canonical
            # jobs.ashbyhq.com job-board URL. ``detect()`` also matches an
            # EMBED — a company's own careers page carrying ``?ashby_jid=``
            # — and there's no org slug in an embed URL to build a
            # canonical target from. Appending /application to an embed's
            # own path (e.g. the company's /careers page) just 404s. So:
            # skip the goto entirely for a non-canonical URL and stay put —
            # the embedded form renders inside an iframe on the current
            # page, which the frame-aware fill primitives (_common.py) can
            # now reach directly.
            current = page.url
            if _is_canonical_form_url(current):
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
            else:
                logger.info(
                    "ashby: URL is not a canonical jobs.ashbyhq.com/<org>/"
                    "<jobId> path (likely a company-page embed) - staying "
                    "put and relying on frame-aware fill"
                )

            # Tolerant readiness wait (Task 3 / #4) — replaces the old
            # ``networkidle`` wait, which analytics-heavy pages routinely
            # never satisfy within 15s, silently degrading a healthy page
            # into an unnecessary hand-off. See ``_common.wait_for_form_ready``.
            readiness = wait_for_form_ready(page, log=logger)
            if not readiness["ready"]:
                logger.warning(
                    "ashby: form-readiness check timed out - proceeding anyway"
                )
            time.sleep(2)  # extra buffer for React hydration

            return run_field_map_fill(
                self, page, job, "ashby",
                screenshot_label=f"ashby_{job.get('id', 'unknown')}",
                resume_path=resume_path,
                cover_letter_path=cover_letter_path,
                note_custom_questions=False,
                readiness_timeout=not readiness["ready"],
                log=logger,
            )

        except Exception as e:
            logger.error(f"Ashby form fill error: {e}")
            return {
                "success": False,
                "notes": f"Error during form fill: {e}",
                "fields_filled": [],
                "required_empty": [],
            }
