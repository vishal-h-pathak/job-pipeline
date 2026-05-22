"""jobpipe.tailor.manual — manual job-URL tailor entry point.

Resolves a pasted job URL into a ``jobs`` row + tailored materials,
bypassing the discovery / scoring pipeline. Wired as
``jobpipe-tailor-one <URL>`` in ``pyproject.toml::[project.scripts]``.

Architecture (per the manual-tailor plan, 2026-05-22):

    URL ─▶ resolve_application_url (aggregator redirects)
         ─▶ detect_ats
         ─▶ per-ATS single-posting fetcher  ──┐
              (greenhouse / lever / ashby)    │ ScrapedPosting
              else generic Playwright scrape  │   {confidence,...}
                                              ▼
                                  upsert_manual_job
                                  ┌───────────────────────┐
                                  │ confidence == 'high'  │ → status='approved'
                                  │                       │   → process_one_approved_job
                                  ├───────────────────────┤
                                  │ confidence == 'low'   │ → status='discovered'
                                  │ (Amendment 1)         │   → return review URL,
                                  │                       │     dashboard takes over
                                  └───────────────────────┘
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

Confidence = Literal["high", "low"]


@dataclass(frozen=True)
class ScrapedPosting:
    """Structured result of scraping a single job posting URL.

    ``confidence='high'`` — a per-ATS structured-API fetcher parsed the
    posting cleanly; the manual flow upserts ``status='approved'`` and
    delegates to ``process_one_approved_job`` for tailoring.

    ``confidence='low'`` — we fell through to the generic HTML scrape
    (or a per-ATS fetcher missing a load-bearing field). The manual
    flow upserts ``status='discovered'`` and surfaces the existing
    dashboard review URL so the human verifies title / company before
    the per-row Tailor button picks the row up (Amendment 1).
    """

    url: str
    title: str
    company: str | None
    location: str | None
    description: str
    ats_kind: str
    confidence: Confidence


class UnsupportedUrl(ValueError):
    """Raised when a per-ATS scraper is handed a URL outside its ATS."""


class ScrapeError(RuntimeError):
    """Raised when the URL is recognized but HTTP / parse fails."""
