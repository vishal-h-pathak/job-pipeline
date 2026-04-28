"""sources/lever.py — Lever postings-API source (J-1).

Direct Lever public-API scanner:
    https://api.lever.co/v0/postings/{slug}?mode=json

Split out of `greenhouse.py` in J-1 so each ATS gets its own module and
the company list lives in `profile/portals.yml`. Pure HTTP/JSON, no LLM
spend on discovery. Each posting is title-pre-filtered before scoring.
"""

import logging
import re
import time

import requests

from config import is_local_or_remote, location_filter_enabled
from sources._portals import companies, passes_title_filter, title_signals
from utils.jobid import make_job_id

logger = logging.getLogger("sources.lever")

TAG_RE = re.compile(r"<[^>]+>")


def _strip_html(text: str) -> str:
    return TAG_RE.sub("", text or "").strip()


# Last-resort fallback if portals.yml is missing. Canonical list lives there.
_FALLBACK_COMPANIES: list[tuple[str, str]] = []


def _fetch_one(slug: str, display_name: str):
    """Fetch open roles from a single Lever board."""
    url = f"https://api.lever.co/v0/postings/{slug}?mode=json"
    try:
        resp = requests.get(url, timeout=15)
        if resp.status_code == 404:
            logger.warning("lever: board %r returned 404 — drop from list", slug)
            return
        resp.raise_for_status()
        data = resp.json()
    except Exception as exc:
        logger.warning("lever: fetch failed for %r: %s", slug, exc)
        return

    raw = 0
    yielded = 0
    skipped_loc = 0
    skipped_title = 0
    for job in data:
        raw += 1
        title = job.get("text", "")
        categories = job.get("categories", {}) or {}
        location = categories.get("location", "Unknown")
        description = job.get("descriptionPlain") or ""
        if not description:
            parts: list[str] = []
            for section in job.get("lists") or []:
                parts.append(section.get("text", ""))
                parts.append(_strip_html(section.get("content", "")))
            description = "\n".join(parts)
        link = job.get("hostedUrl") or job.get("applyUrl") or ""

        if not passes_title_filter(title):
            skipped_title += 1
            continue

        if location_filter_enabled() and not is_local_or_remote(location):
            skipped_loc += 1
            continue

        signals = title_signals(title)
        if signals["prefer"] or signals["seniority"]:
            logger.debug("lever: %s title signals %s on %r", slug, signals, title)

        yielded += 1
        yield {
            "id": make_job_id(link, title, display_name),
            "source": "lever",
            "query": "",
            "title": title,
            "company": display_name,
            "location": location,
            "description": description[:3000],
            "url": link,
        }
    logger.info(
        "lever: %s yielded=%d (raw=%d, title-filtered=%d, location-filtered=%d)",
        slug, yielded, raw, skipped_title, skipped_loc,
    )


def fetch():
    """Yield job dicts from every Lever board listed in portals.yml."""
    targets = companies("lever") or _FALLBACK_COMPANIES
    for slug, name in targets:
        yield from _fetch_one(slug, name)
        time.sleep(0.5)
