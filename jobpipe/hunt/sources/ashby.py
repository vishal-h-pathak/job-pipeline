"""sources/ashby.py — AshbyHQ public posting API.

Many AI-first startups host their careers pages on Ashby (notion.com,
linear.app, replicate.com, modal.com, sierra.ai, etc.). Ashby exposes a
public posting API per organization at:

    https://api.ashbyhq.com/posting-api/job-board/{org_slug}?includeCompensation=true

The response is JSON with a ``jobs`` array; each job carries title,
location, departmentName, descriptionHtml, jobUrl, and a few other fields
documented at https://developers.ashbyhq.com/.

This module mirrors the shape of ``greenhouse.py``: a hand-curated list of
target organizations, keyword filtering, and mode-aware location filtering
(``local_remote`` keeps Atlanta / remote / unknown only). When you discover
a new Ashby-using company, drop a row in ``ASHBY_COMPANIES`` and ship.

To verify a slug is correct, paste this URL in a browser:
    https://api.ashbyhq.com/posting-api/job-board/<slug>
A valid org returns JSON; an unknown slug returns ``{"jobs": []}`` or 404.
"""

from __future__ import annotations

import logging
import re
import time

import requests

from config import is_local_or_remote, location_filter_enabled
from utils.jobid import make_job_id

logger = logging.getLogger("sources.ashby")

TAG_RE = re.compile(r"<[^>]+>")


def _strip_html(text: str) -> str:
    return TAG_RE.sub("", text or "").strip()


# ── Curated Ashby boards ──────────────────────────────────────────────────
# Keep this conservative. Slugs that don't resolve will log a warning and
# be skipped — they do not break the run. To verify an org: try the URL
# above in your browser. The first few entries are confirmed; the
# commented-out ones are guesses worth double-checking before adding.
ASHBY_COMPANIES = [
    # ── AI infrastructure / inference platforms ────────────────────────
    ("ashby", "replicate", "Replicate"),
    ("ashby", "modal-labs", "Modal Labs"),
    ("ashby", "posthog", "PostHog"),
    ("ashby", "linear", "Linear"),
    ("ashby", "notion", "Notion"),
    # ── Foundation-model adjacent / agentic AI ─────────────────────────
    ("ashby", "decagon", "Decagon"),
    ("ashby", "sierra", "Sierra"),
    ("ashby", "glean", "Glean"),
    ("ashby", "sakanaai", "Sakana AI"),
    ("ashby", "rekaai", "Reka AI"),
    ("ashby", "factoryai", "Factory AI"),
    # ── Vector DB / retrieval (sales-eng targets) ──────────────────────
    ("ashby", "pineconeio", "Pinecone"),
    ("ashby", "weaviate", "Weaviate"),
    # ── Add neuro / BCI / connectomics orgs as discovered ──────────────
    # (The neurotech crowd skews Lever/Greenhouse so far; this list is
    #  intentionally biased toward the AI startups Ashby tends to host.)
]

# Same keyword filter strategy as greenhouse.py — broad enough to catch
# relevant roles, narrow enough to skip recruiter and ops postings.
KEYWORDS = (
    "neuromorphic", "neuroscience", "neural", "spiking", "connectom",
    "brain", "bci", "neuroprosth", "neurotech",
    "machine learning", "ml engineer", "computer vision",
    "embedded", "fpga", "vhdl", "rtl",
    "sales engineer", "solutions engineer", "developer relations",
    "developer advocate", "developer experience",
    "ai engineer", "applied scientist", "research engineer",
    "platform engineer", "sdk", "tools",
)


def _matches(text: str) -> bool:
    text = (text or "").lower()
    return any(kw in text for kw in KEYWORDS)


def _fetch_one(slug: str, display_name: str):
    """Fetch open roles from one Ashby board."""
    url = f"https://api.ashbyhq.com/posting-api/job-board/{slug}?includeCompensation=true"
    try:
        resp = requests.get(url, timeout=15)
        if resp.status_code == 404:
            logger.warning("ashby: board %r returned 404 — drop from list", slug)
            return
        resp.raise_for_status()
        data = resp.json()
    except Exception as exc:
        logger.warning("ashby: fetch failed for %r: %s", slug, exc)
        return

    raw = 0
    yielded = 0
    skipped_loc = 0
    for job in data.get("jobs", []):
        # Some boards expose unlisted drafts; skip when the API flags them.
        if not job.get("isListed", True):
            continue
        raw += 1
        title = job.get("title") or ""
        location = job.get("location") or "Unknown"
        # Description ships as HTML; flatten for the scorer + dashboard.
        description = _strip_html(job.get("descriptionHtml") or job.get("descriptionPlain") or "")
        link = job.get("jobUrl") or job.get("applyUrl") or ""

        if not _matches(f"{title} {description}"):
            continue
        if location_filter_enabled() and not is_local_or_remote(location):
            skipped_loc += 1
            continue

        yielded += 1
        yield {
            "id": make_job_id(link, title, display_name),
            "source": "ashby",
            "query": "",
            "title": title,
            "company": display_name,
            "location": location,
            "description": description[:3000],
            "url": link,
        }
    logger.info("ashby: %s yielded=%d (raw=%d, location-filtered=%d)",
                slug, yielded, raw, skipped_loc)


def fetch():
    """Yield job dicts from every tracked Ashby board."""
    for _, slug, name in ASHBY_COMPANIES:
        yield from _fetch_one(slug, name)
        time.sleep(0.5)
