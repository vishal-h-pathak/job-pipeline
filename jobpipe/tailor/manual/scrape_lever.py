"""Lever single-posting fetcher for the manual-tailor flow.

Lever exposes a public JSON endpoint per posting:

    https://api.lever.co/v0/postings/{slug}/{posting_id}?mode=json

The endpoint does NOT carry a company display name — the slug is the
canonical company identifier on Lever. We surface it as
``slug.replace('-', ' ').title()`` which is right for most slugs
("anthropic", "scale-ai") but imperfect for acronyms ("openai" →
"Openai"). The dashboard review surface lets the user fix it; we
still mark confidence='high' because title + description came from
a structured API.

URL shapes accepted:
    https://jobs.lever.co/{slug}/{posting_id}
    https://jobs.lever.co/{slug}/{posting_id}/apply

Tracking params (``lever-source``, ``utm_*``) are dropped by
``canonical_url`` before the row id is computed.
"""

from __future__ import annotations

import re
from urllib.parse import urlparse

import httpx

from jobpipe.shared.html import strip_tags
from jobpipe.shared.jobid import canonical_url

from . import ScrapedPosting, UnsupportedUrl, ScrapeError

_LEVER_HOSTS = ("jobs.lever.co", "jobs.eu.lever.co")
_URL_PATTERN = re.compile(
    r"^/(?P<slug>[^/]+)/(?P<pid>[a-zA-Z0-9-]{6,})(?:/apply)?/?$"
)
_POSTINGS_API = "https://api.lever.co/v0/postings"


def _compose_description(posting: dict) -> str:
    """Flatten Lever's split description shape (descriptionPlain + lists +
    additionalPlain) into a single plain-text blob.
    """
    parts: list[str] = []
    desc_plain = (posting.get("descriptionPlain") or "").strip()
    if desc_plain:
        parts.append(desc_plain)
    # If the posting only ships HTML (no descriptionPlain), strip it.
    elif posting.get("description"):
        parts.append(strip_tags(posting["description"]))

    for section in posting.get("lists") or []:
        heading = (section.get("text") or "").strip()
        body = strip_tags(section.get("content") or "")
        if heading and body:
            parts.append(f"{heading}:\n{body}")
        elif body:
            parts.append(body)

    extra = (posting.get("additionalPlain") or "").strip()
    if not extra and posting.get("additional"):
        extra = strip_tags(posting["additional"])
    if extra:
        parts.append(extra)

    return "\n\n".join(parts).strip()


def _company_from_slug(slug: str) -> str:
    """Best-effort company display name from a Lever slug.

    Right for "anthropic" → "Anthropic" and "scale-ai" → "Scale Ai".
    Wrong for acronym-shaped slugs ("openai" → "Openai") — the review
    surface lets the human correct that.
    """
    return slug.replace("-", " ").replace("_", " ").strip().title()


def fetch_lever_posting(
    url: str, *, timeout: float = 15.0
) -> ScrapedPosting:
    """Fetch a single Lever posting and return a ScrapedPosting.

    Raises:
        UnsupportedUrl: ``url`` is not a Lever posting URL.
        ScrapeError: the URL parses but HTTP or JSON fails.
    """
    parsed = urlparse(url)
    host = (parsed.hostname or "").lower()
    if host not in _LEVER_HOSTS:
        raise UnsupportedUrl(f"not a Lever posting URL: {url}")

    m = _URL_PATTERN.match(parsed.path or "")
    if not m:
        raise UnsupportedUrl(
            f"unrecognized Lever URL shape: {url} "
            "(expected /<slug>/<posting_id>)"
        )
    slug, pid = m.group("slug"), m.group("pid")

    api = f"{_POSTINGS_API}/{slug}/{pid}?mode=json"

    try:
        with httpx.Client(timeout=timeout) as client:
            resp = client.get(api)
            resp.raise_for_status()
            posting = resp.json()
    except (httpx.HTTPError, ValueError) as exc:
        raise ScrapeError(f"lever fetch failed for {url}: {exc}") from exc

    title = (posting.get("text") or "").strip()
    if not title:
        raise ScrapeError(f"lever posting {url} has no title")

    location = (
        ((posting.get("categories") or {}).get("location") or "").strip() or None
    )
    description = _compose_description(posting)
    company = _company_from_slug(slug)
    final_url = posting.get("hostedUrl") or url

    return ScrapedPosting(
        url=canonical_url(final_url),
        title=title,
        company=company,
        location=location,
        description=description,
        ats_kind="lever",
        confidence="high",
    )
