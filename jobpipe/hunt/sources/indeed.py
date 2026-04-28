"""
sources/indeed.py — Indeed RSS feed fetcher.

KEEP-DISABLED (PR-3): this module is intentionally NOT included in the
``SOURCES`` tuple in ``hunt/agent.py``. Indeed RSS has been fully gated
for unauthenticated callers since early 2026 and JSearch covers the same
publisher footprint behind one paid subscription. We keep the module on
disk for reference (the URL templates and per-(query, location) feed
shape remain useful documentation) but it is not exercised at runtime.
Re-enabling means flipping it back into the ``SOURCES`` tuple, not
ripping the file out.

Notes on reliability (April 2026): Indeed has been progressively gating its
public RSS feeds and routinely returns empty <channel>s for unauthenticated
callers. We do three things to defend against silent breakage:

1. Send a real User-Agent — feedparser's default UA is blocked outright on
   some Indeed paths.
2. Log per-(query, location) entry counts so an empty feed shows up in
   ``agent.log`` instead of vanishing.
3. Tolerate ``feed.bozo`` / parser exceptions and keep iterating other
   queries; we'd rather lose one query than the whole source.

If Indeed RSS goes fully dark, the right move is to swap to the SerpAPI
``site:indeed.com`` queries already implemented in ``linkedin.py`` style.
"""

from __future__ import annotations

import logging
import time
from urllib.parse import urlencode

import feedparser

from config import get_mode
from jobpipe.shared.html import strip_tags
from utils.jobid import make_job_id

logger = logging.getLogger("sources.indeed")

QUERIES = [
    "neuromorphic",
    "computational neuroscience",
    "spiking neural network",
    "connectomics",
    "sales engineer LLM",
    "sales engineer AI",
    "sales engineer neuromorphic",
    "sales engineer brain computer interface",
    "technical sales AI startup",
    "solutions engineer machine learning",
    "developer relations AI",
    "developer advocate machine learning",
]

# Mode-aware location set. ``us_wide`` adds the national fallback; the others
# hold the high-signal locations we always want.
_LOCAL_REMOTE_LOCATIONS = (
    {"l": "Atlanta, GA", "label": "Atlanta, GA"},
    {"l": "Remote", "label": "Remote"},
)
_US_EXTRA_LOCATIONS = (
    {"l": "United States", "label": "United States"},
)

BASE = "https://www.indeed.com/rss"

# Indeed silently drops requests with feedparser's default UA. Use a real
# browser string and an Accept header that matches what curl/Safari send.
_USER_AGENT = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 14_0) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/121.0 Safari/537.36"
)
_REQUEST_HEADERS = {
    "User-Agent": _USER_AGENT,
    "Accept": "application/rss+xml, application/xml;q=0.9, */*;q=0.8",
    "Accept-Language": "en-US,en;q=0.9",
}


def _locations_for_mode():
    if get_mode() == "us_wide":
        return _LOCAL_REMOTE_LOCATIONS + _US_EXTRA_LOCATIONS
    return _LOCAL_REMOTE_LOCATIONS


def fetch():
    """Yield job dicts from Indeed RSS across the keyword/location matrix."""
    seen_local: set[str] = set()
    locations = _locations_for_mode()
    total_entries = 0
    empty_feeds = 0

    for q in QUERIES:
        for loc in locations:
            url = f"{BASE}?{urlencode({'q': q, 'l': loc['l']})}"
            try:
                feed = feedparser.parse(url, request_headers=_REQUEST_HEADERS)
            except Exception as exc:  # pragma: no cover — feedparser rarely raises
                logger.warning("indeed feed exception q=%r loc=%r: %s", q, loc["label"], exc)
                continue

            entry_count = len(feed.entries or [])
            if entry_count == 0:
                empty_feeds += 1
                logger.info("indeed: 0 entries for q=%r loc=%r (bozo=%s)",
                            q, loc["label"], getattr(feed, "bozo", False))
            else:
                logger.info("indeed: %d entries for q=%r loc=%r",
                            entry_count, q, loc["label"])

            for entry in feed.entries:
                title_raw = entry.get("title", "")
                # Indeed RSS titles are typically "Job Title - Company - Location"
                parts = [p.strip() for p in title_raw.split(" - ")]
                title = parts[0] if parts else title_raw
                company = parts[1] if len(parts) > 1 else "Unknown"
                location = parts[2] if len(parts) > 2 else loc["label"]
                link = entry.get("link", "")
                description = strip_tags(entry.get("summary", ""))
                jid = make_job_id(link, title, company)
                if jid in seen_local:
                    continue
                seen_local.add(jid)
                total_entries += 1
                yield {
                    "id": jid,
                    "source": "indeed",
                    "query": q,
                    "title": title,
                    "company": company,
                    "location": location,
                    "description": description,
                    "url": link,
                }
            time.sleep(1)

    logger.info("indeed total: %d unique entries (%d empty feeds out of %d)",
                total_entries, empty_feeds, len(QUERIES) * len(locations))
