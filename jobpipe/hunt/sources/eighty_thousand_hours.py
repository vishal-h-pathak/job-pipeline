"""sources/eighty_thousand_hours.py — 80,000 Hours job board (via Algolia).

The 80,000 Hours job board is a Nuxt SPA at https://jobs.80000hours.org
backed by Algolia. We bypass the front-end and query Algolia directly
using their public search-only credentials (verified 2026-04-26 by
inspecting their network requests).

The defaults below are baked in because the credentials are search-only —
they're served to every visitor of the page in plaintext as part of the
Nuxt config. If 80kh rotates them or migrates off Algolia, override via
``.env``:

    ALGOLIA_80KH_APP_ID=...
    ALGOLIA_80KH_API_KEY=...
    ALGOLIA_80KH_INDEX=...

To find new values: open https://jobs.80000hours.org/ in Chrome DevTools,
search the rendered HTML for ``algoliaApplicationId`` — Nuxt embeds the
config there.
"""

from __future__ import annotations

import logging
import os
import re
from html import unescape

import requests

from utils.jobid import make_job_id

logger = logging.getLogger("sources.eighty_thousand_hours")

# Keyword filter — same broad set as the other sources. Tuned for Tier 1
# (mission-driven research) and Tier 3 (mission-driven ML/CV). Tier 2 sales
# eng roles rarely appear on 80kh so we don't try to cover them here.
KEYWORDS = (
    "neuro", "brain", "bci", "spiking", "connectom",
    "machine learning", "ml ", "ai engineer", "ai safety", "alignment",
    "computer vision", "embedded", "fpga",
    "research engineer", "applied scientist",
    "platform engineer", "sdk", "tools",
    "engineer",
)

TAG_RE = re.compile(r"<[^>]+>")


def _strip_html(text: str) -> str:
    return TAG_RE.sub(" ", unescape(text or "")).strip()


def _matches(text: str) -> bool:
    text = (text or "").lower()
    return any(kw in text for kw in KEYWORDS)


def _algolia_query(app_id: str, api_key: str, index: str,
                   query: str, hits_per_page: int = 50) -> list[dict]:
    """Run a single Algolia query against the 80kh job-board index."""
    url = f"https://{app_id}-dsn.algolia.net/1/indexes/{index}/query"
    headers = {
        "X-Algolia-Application-Id": app_id,
        "X-Algolia-API-Key": api_key,
        "Content-Type": "application/json",
    }
    payload = {
        "query": query,
        "hitsPerPage": hits_per_page,
        "page": 0,
    }
    try:
        resp = requests.post(url, headers=headers, json=payload, timeout=15)
        resp.raise_for_status()
        return (resp.json() or {}).get("hits", []) or []
    except Exception as exc:
        logger.warning("80kh: Algolia query %r failed: %s", query, exc)
        return []


# Defaults harvested 2026-04-26 from the live page's embedded Nuxt
# config. Search-only key = safe to commit. ``.env`` overrides win.
_DEFAULT_APP_ID = "W6KM1UDIB3"
_DEFAULT_API_KEY = "d1d7f2c8696e7b36837d5ed337c4a319"
_DEFAULT_INDEX = "jobs_prod"


def _flatten_locations(loc):
    """80kh stores locations as either a string or a list of region strings.
    We always return a single comma-separated string for downstream consumers.
    """
    if loc is None:
        return ""
    if isinstance(loc, list):
        return ", ".join(str(x) for x in loc if x)
    if isinstance(loc, dict):
        return loc.get("name") or loc.get("city") or loc.get("country") or ""
    return str(loc)


def fetch():
    """Yield job dicts from the 80,000 Hours job board via Algolia."""
    app_id = os.environ.get("ALGOLIA_80KH_APP_ID") or _DEFAULT_APP_ID
    api_key = os.environ.get("ALGOLIA_80KH_API_KEY") or _DEFAULT_API_KEY
    index = os.environ.get("ALGOLIA_80KH_INDEX") or _DEFAULT_INDEX

    if not (app_id and api_key and index):
        logger.info("80kh: missing Algolia config — skipping")
        return

    # Run a small set of focused queries. Algolia handles synonyms and
    # ranking, so we don't need to enumerate every keyword.
    queries = [
        "machine learning engineer",
        "research engineer",
        "AI safety",
        "neuroscience",
        "computer vision",
        "developer tools",
    ]

    seen_local: set[str] = set()
    raw = 0
    yielded = 0

    for q in queries:
        hits = _algolia_query(app_id, api_key, index, q)
        for hit in hits:
            raw += 1
            # Algolia hits expose flat fields; their exact names depend on
            # the index schema. We probe several common shapes so the
            # adapter survives moderate rename drift.
            title = (
                hit.get("title")
                or hit.get("role")
                or hit.get("position")
                or ""
            )
            company = (
                hit.get("company")
                or hit.get("organization")
                or hit.get("employer")
                or "Unknown"
            )
            if isinstance(company, dict):
                company = company.get("name") or "Unknown"
            location = _flatten_locations(
                hit.get("location")
                or hit.get("locations")
                or hit.get("city")
                or hit.get("country")
                or "Unknown"
            )
            description = _strip_html(
                hit.get("description")
                or hit.get("summary")
                or hit.get("about")
                or ""
            )
            link = (
                hit.get("apply_url")
                or hit.get("url")
                or hit.get("hosted_url")
                or hit.get("link")
                or ""
            )
            if not (title and link):
                continue
            if not _matches(f"{title} {description}"):
                continue

            jid = make_job_id(link, title, str(company))
            if jid in seen_local:
                continue
            seen_local.add(jid)
            yielded += 1
            yield {
                "id": jid,
                "source": "80kh",
                "query": q,
                "title": title,
                "company": str(company),
                "location": str(location),
                "description": description[:3000],
                "url": link,
            }
        logger.info("80kh: q=%r returned %d hits", q, len(hits))

    logger.info("80kh total: yielded=%d (raw hits=%d, queries=%d)",
                yielded, raw, len(queries))
