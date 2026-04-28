"""sources/hn_whoshiring.py — HackerNews "Ask HN: Who is hiring?" via Algolia.

Every month dang posts an "Ask HN: Who is hiring? (Month YYYY)" thread on
HN. Each top-level comment is a hiring company. The format is loose but
enough is conventional that we can extract usable rows:

    LINE 1:  "Company | Role | Location | Comp | Remote/Hybrid"
    BODY:    free text describing the role + an apply URL or email

This module:
  1. Asks the public Algolia HN search API for the most recent matching
     thread (no API key required).
  2. Pulls the full thread (including all top-level comments) via the HN
     Algolia items endpoint.
  3. Filters comments by keywords (same set as the other sources).
  4. Extracts a company name from the first line and the first http(s) URL
     from the body. Rows where neither can be parsed are skipped.

Reliability note: HN's signal-to-noise is high for AI/ML startups but the
parsing is best-effort. Expect ~70-80% of yielded rows to look clean.
"""

from __future__ import annotations

import logging
import re
import time

import requests

from jobpipe.shared.html import clean_html_to_text
from utils.jobid import make_job_id

logger = logging.getLogger("sources.hn_whoshiring")

ALGOLIA_SEARCH = "https://hn.algolia.com/api/v1/search"
ALGOLIA_ITEM = "https://hn.algolia.com/api/v1/items/{}"

URL_RE = re.compile(r"https?://[^\s<>\"')]+")

# Same keyword filter as the other sources, slightly broader to catch the
# many "AI engineer" / "ML engineer" framings HN posters use.
KEYWORDS = (
    "neuromorphic", "neuroscience", "neural network", "spiking", "connectom",
    "brain-computer", " bci ", "neurotech",
    "machine learning", "ml engineer", "ml/ai", "ai engineer", "ai/ml",
    "computer vision", "embedded ml", "fpga", "vhdl",
    "sales engineer", "solutions engineer", "developer relations",
    "developer advocate", "developer experience",
    "applied scientist", "research engineer",
    "platform engineer", "sdk",
)


def _matches(text: str) -> bool:
    text = (text or "").lower()
    return any(kw in text for kw in KEYWORDS)


def _find_thread_id() -> str | None:
    """Return the HN story id of the most recent 'Who is hiring?' thread."""
    params = {
        "query": "Ask HN Who is hiring",
        "tags": "story,author_whoishiring",
        "hitsPerPage": 5,
    }
    try:
        resp = requests.get(ALGOLIA_SEARCH, params=params, timeout=15)
        resp.raise_for_status()
        hits = resp.json().get("hits") or []
    except Exception as exc:
        logger.warning("hn: Algolia search failed: %s", exc)
        return None

    # whoishiring posts both "Who is hiring?" and "Who wants to be hired?"
    # threads. Pick the most recent one whose title starts with "Ask HN:
    # Who is hiring".
    for hit in sorted(hits, key=lambda h: h.get("created_at_i", 0), reverse=True):
        title = (hit.get("title") or "").lower()
        if title.startswith("ask hn: who is hiring"):
            return str(hit.get("objectID"))
    return None


def _extract_company(line: str) -> str:
    """First line is conventionally 'Company | Role | Location | …'.
    Take the part before the first separator we find."""
    for sep in (" | ", " - ", " — ", "–"):
        if sep in line:
            return line.split(sep, 1)[0].strip()
    # Fallback: use up to the first 60 chars of the first line.
    return line.strip()[:60] or "Unknown"


def _walk_comments(node: dict):
    """Yield every descendant comment of an HN item tree."""
    for child in node.get("children", []) or []:
        yield child
        yield from _walk_comments(child)


def fetch():
    """Yield job dicts parsed from the latest HN Who-is-hiring thread."""
    thread_id = _find_thread_id()
    if not thread_id:
        logger.warning("hn: no Who-is-hiring thread found in Algolia search")
        return

    try:
        resp = requests.get(ALGOLIA_ITEM.format(thread_id), timeout=20)
        resp.raise_for_status()
        thread = resp.json()
    except Exception as exc:
        logger.warning("hn: thread %s fetch failed: %s", thread_id, exc)
        return

    title = thread.get("title") or f"HN thread {thread_id}"
    logger.info("hn: thread %s — %r", thread_id, title)

    raw = 0
    yielded = 0
    seen_local: set[str] = set()
    for comment in _walk_comments(thread):
        text = comment.get("text") or ""
        if not text:
            continue
        # Top-level comments live at depth 1 (parent = thread); deeper
        # replies are conversations we don't want.
        if comment.get("parent_id") != int(thread_id):
            continue
        raw += 1
        plain = clean_html_to_text(text)
        if not _matches(plain):
            continue

        # First "line" — Algolia returns text with <p> separators that
        # clean_html_to_text collapses tags into spaces; split on the first sentence
        # boundary or pipe to recover the conventional header.
        first_line = plain.split(".", 1)[0]
        for marker in ("\n", " | "):
            if marker in first_line:
                first_line = first_line.split(marker, 1)[0]
                break
        company = _extract_company(first_line)

        url_match = URL_RE.search(text)
        link = url_match.group(0).rstrip(".,;:)") if url_match else ""

        # Use everything before the apply URL as title-ish + description;
        # keep the title short so the dashboard renders cleanly.
        title_guess = first_line[:120].strip()
        if not title_guess:
            continue

        jid = make_job_id(link or f"hn:{comment.get('id')}", title_guess, company)
        if jid in seen_local:
            continue
        seen_local.add(jid)
        yielded += 1
        yield {
            "id": jid,
            "source": "hn_whoshiring",
            "query": "",
            "title": title_guess,
            "company": company,
            "location": "See description",
            "description": plain[:3000],
            "url": link or f"https://news.ycombinator.com/item?id={comment.get('id')}",
        }

    logger.info("hn: yielded=%d (raw top-level comments=%d)", yielded, raw)
    time.sleep(0.5)
