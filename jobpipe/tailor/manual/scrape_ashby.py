"""Ashby single-posting fetcher for the manual-tailor flow.

The Ashby public job-board API returns ALL postings for a company in
one JSON document; there's no single-posting endpoint. We fetch the
board and filter by id parsed from the URL — mirroring the field
mapping used by hunt's ``sources/ashby.py``.

    https://api.ashbyhq.com/posting-api/job-board/{slug}?includeCompensation=true

The endpoint does NOT carry a company display name (hunt's source
pulls display names from ``profile/portals.yml``). For the manual
flow we derive it as ``slug.replace('-', ' ').title()`` — same
limitation as Lever. The dashboard review surface lets the human
correct it.

URL shapes accepted:
    https://jobs.ashbyhq.com/{slug}/{posting_id}
    https://jobs.ashbyhq.com/{slug}/{posting_id}/application
"""

from __future__ import annotations

import re
from urllib.parse import urlparse

import httpx

from jobpipe.shared.html import strip_tags
from jobpipe.shared.jobid import canonical_url

from . import ScrapedPosting, UnsupportedUrl, ScrapeError

_ASHBY_HOSTS = ("jobs.ashbyhq.com", "ashbyhq.com")
_URL_PATTERN = re.compile(
    r"^/(?P<slug>[^/]+)/(?P<pid>[a-zA-Z0-9-]{6,})(?:/application)?/?$"
)
_BOARD_API = "https://api.ashbyhq.com/posting-api/job-board"


def _company_from_slug(slug: str) -> str:
    return slug.replace("-", " ").replace("_", " ").strip().title()


def fetch_ashby_posting(
    url: str, *, timeout: float = 15.0
) -> ScrapedPosting:
    """Fetch a single Ashby posting (by filtering the board) and return a ScrapedPosting.

    Raises:
        UnsupportedUrl: ``url`` is not an Ashby posting URL.
        ScrapeError: the URL parses but HTTP / JSON fails OR the posting id
            isn't present in the board response.
    """
    parsed = urlparse(url)
    host = (parsed.hostname or "").lower()
    if host not in _ASHBY_HOSTS:
        raise UnsupportedUrl(f"not an Ashby posting URL: {url}")

    m = _URL_PATTERN.match(parsed.path or "")
    if not m:
        raise UnsupportedUrl(
            f"unrecognized Ashby URL shape: {url} "
            "(expected /<slug>/<posting_id>)"
        )
    slug, pid = m.group("slug"), m.group("pid")

    board_api = f"{_BOARD_API}/{slug}?includeCompensation=true"

    try:
        with httpx.Client(timeout=timeout) as client:
            resp = client.get(board_api)
            resp.raise_for_status()
            board = resp.json()
    except (httpx.HTTPError, ValueError) as exc:
        raise ScrapeError(f"ashby fetch failed for {url}: {exc}") from exc

    postings = board.get("jobs") or []
    match = next((j for j in postings if str(j.get("id") or "") == pid), None)
    if match is None:
        raise ScrapeError(
            f"ashby posting {pid} not found in board {slug} "
            f"(board has {len(postings)} listed jobs)"
        )

    title = (match.get("title") or "").strip()
    if not title:
        raise ScrapeError(f"ashby posting {url} has no title")

    location = (match.get("location") or "").strip() or None
    description = strip_tags(
        match.get("descriptionHtml") or match.get("descriptionPlain") or ""
    )
    company = _company_from_slug(slug)
    final_url = match.get("jobUrl") or url

    return ScrapedPosting(
        url=canonical_url(final_url),
        title=title,
        company=company,
        location=location,
        description=description,
        ats_kind="ashby",
        confidence="high",
    )
