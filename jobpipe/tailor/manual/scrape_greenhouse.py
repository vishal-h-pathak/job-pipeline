"""Greenhouse single-posting fetcher for the manual-tailor flow.

Reuses the public boards-api JSON endpoints the hunt's
``sources/greenhouse.py`` already uses, but hits the *single-posting*
shape:

    https://boards-api.greenhouse.io/v1/boards/{slug}/jobs/{id}?content=true

Plus one extra call to ``/v1/boards/{slug}`` for the company display
name — the single-job endpoint doesn't carry the company string,
only its slug. Both calls share one ``httpx.Client`` context so the
test suite mocks with a single patch.

URL shapes accepted:
    https://job-boards.greenhouse.io/{slug}/jobs/{id}
    https://boards.greenhouse.io/{slug}/jobs/{id}        (alias)
    https://boards.eu.greenhouse.io/{slug}/jobs/{id}     (EU alias)

Tracking query params (``gh_jid``, ``utm_*``) are dropped by
``canonical_url`` before the row id is computed.
"""

from __future__ import annotations

import re
from urllib.parse import urlparse

import httpx

from jobpipe.shared.html import strip_tags
from jobpipe.shared.jobid import canonical_url

from . import ScrapedPosting, UnsupportedUrl, ScrapeError

_GREENHOUSE_HOSTS = (
    "boards.greenhouse.io",
    "job-boards.greenhouse.io",
    "boards.eu.greenhouse.io",
)
_URL_PATTERN = re.compile(r"^/(?P<slug>[^/]+)/jobs/(?P<jid>\d+)/?$")
_BOARDS_API = "https://boards-api.greenhouse.io/v1/boards"


def fetch_greenhouse_posting(
    url: str, *, timeout: float = 15.0
) -> ScrapedPosting:
    """Fetch a single Greenhouse posting and return a ScrapedPosting.

    Raises:
        UnsupportedUrl: ``url`` is not a Greenhouse posting URL.
        ScrapeError: the URL parses but HTTP or JSON fails.
    """
    parsed = urlparse(url)
    host = (parsed.hostname or "").lower()
    if host not in _GREENHOUSE_HOSTS:
        raise UnsupportedUrl(f"not a Greenhouse posting URL: {url}")

    m = _URL_PATTERN.match(parsed.path or "")
    if not m:
        raise UnsupportedUrl(
            f"unrecognized Greenhouse URL shape: {url} "
            "(expected /<slug>/jobs/<id>)"
        )
    slug, jid = m.group("slug"), m.group("jid")

    job_api = f"{_BOARDS_API}/{slug}/jobs/{jid}?content=true"
    board_api = f"{_BOARDS_API}/{slug}"

    try:
        with httpx.Client(timeout=timeout) as client:
            job_resp = client.get(job_api)
            job_resp.raise_for_status()
            job = job_resp.json()
            board_resp = client.get(board_api)
            board_resp.raise_for_status()
            board = board_resp.json()
    except (httpx.HTTPError, ValueError) as exc:
        raise ScrapeError(f"greenhouse fetch failed for {url}: {exc}") from exc

    title = (job.get("title") or "").strip()
    if not title:
        raise ScrapeError(f"greenhouse posting {url} has no title")

    location = ((job.get("location") or {}).get("name") or "").strip() or None
    description = strip_tags(job.get("content") or "")
    company = (board.get("name") or "").strip() or None

    # Prefer the API's absolute_url over the input — it normalises
    # boards.greenhouse.io → job-boards.greenhouse.io ahead of canonical_url.
    final_url = job.get("absolute_url") or url

    return ScrapedPosting(
        url=canonical_url(final_url),
        title=title,
        company=company,
        location=location,
        description=description,
        ats_kind="greenhouse",
        confidence="high",
    )
