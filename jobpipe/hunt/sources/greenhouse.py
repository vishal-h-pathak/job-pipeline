"""Greenhouse & Lever job board source.

Many startups and neuro-adjacent companies post on Greenhouse or Lever,
which expose clean JSON APIs at predictable URLs:
  - Greenhouse: https://boards-api.greenhouse.io/v1/boards/{company}/jobs
  - Lever:      https://api.lever.co/v0/postings/{company}

This module maintains a curated list of target companies and pulls all
open roles, yielding only those that match keyword filters. No API key
needed — these are public endpoints.

Mode handling: in ``local_remote`` mode roles whose ``location`` is neither
Atlanta/GA nor remote-shaped are skipped. ``us_wide`` keeps everything.
"""

import logging
import re
import time

import requests

from config import is_local_or_remote, location_filter_enabled
from utils.jobid import make_job_id

logger = logging.getLogger("sources.greenhouse")

TAG_RE = re.compile(r"<[^>]+>")


def _strip_html(text: str) -> str:
    return TAG_RE.sub("", text or "").strip()


# ── Target companies ─────────────────────────────────────────────────
# Add companies here as you discover them. Format:
#   ("platform", "board_slug", "display_name")

# Curated Greenhouse boards. Slugs that 404 are dropped silently with a log
# warning so the rest of the run continues. To add a company: visit
# ``boards.greenhouse.io/<slug>`` in a browser and confirm it loads, then
# add the row here. Vet on first run via ``agent.log``.
GREENHOUSE_COMPANIES = [
    # ── Confirmed via 2026-04-26 run ───────────────────────────────────
    # These slugs returned 200 in the hunter run we observed.
    ("greenhouse", "neuralink", "Neuralink"),
    ("greenhouse", "anthropic", "Anthropic"),
    ("greenhouse", "deepmind", "DeepMind"),
    ("greenhouse", "scaleai", "Scale AI"),
    ("greenhouse", "recursionpharma", "Recursion"),
    ("greenhouse", "insitro", "Insitro"),
    # ── Verified 200 but not yet in production run ─────────────────────
    ("greenhouse", "databricks", "Databricks"),
    ("greenhouse", "anyscale", "Anyscale"),
    ("greenhouse", "weightsandbiases", "Weights & Biases"),
    ("greenhouse", "runwayml", "Runway"),
    ("greenhouse", "stabilityai", "Stability AI"),
    ("greenhouse", "atomwise", "Atomwise"),
    ("greenhouse", "isomorphiclabs", "Isomorphic Labs"),
    # ── KNOWN-DEAD slugs (404) ─ left here as documentation ───────────
    # These returned 404 on 2026-04-26. Either the company moved off
    # Greenhouse, the slug is different than expected, or the board is
    # private. To re-add, find the correct slug at boards.greenhouse.io
    # or boards-api.greenhouse.io/v1/boards/<slug>/jobs and verify before
    # uncommenting. Keeping them as comments avoids re-guessing every run.
    #     brainchipinc, innatera, numenta            (probably not on Greenhouse)
    #     openai                                     (was greenhouse — slug may have changed)
    #     cohere                                     (slug may be 'cohereinc' or similar)
    #     huggingface                                (try 'huggingfaceinc')
    #     mistralai, perplexityai, characterai       (likely Workable / Ashby)
]

LEVER_COMPANIES = [
    # ── Neurotech / BCI (Tier 1) ───────────────────────────────────────
    ("lever", "rainai", "Rain AI"),
    ("lever", "synchron", "Synchron"),
    ("lever", "paradromics", "Paradromics"),
    ("lever", "cortical", "Cortical Labs"),
    # ── AI labs / infra ────────────────────────────────────────────────
    ("lever", "groq", "Groq"),
    ("lever", "togetherai", "Together AI"),
    ("lever", "writer", "Writer"),
]

# Keywords to match against title + description.
# Broad enough to catch relevant roles but narrow enough to avoid noise.
KEYWORDS = [
    "neuromorphic", "neuroscience", "neural", "spiking", "connectom",
    "brain", "bci", "neuroprosth", "neurotech",
    "machine learning", "ml engineer", "computer vision",
    "embedded", "fpga", "vhdl", "rtl",
    "sales engineer", "solutions engineer", "developer relations",
    "ai engineer", "applied scientist", "research engineer",
    "platform engineer", "sdk", "developer experience",
]


def _matches(text: str) -> bool:
    text = text.lower()
    return any(kw in text for kw in KEYWORDS)


def _fetch_greenhouse(slug: str, display_name: str):
    """Fetch open roles from a Greenhouse board."""
    url = f"https://boards-api.greenhouse.io/v1/boards/{slug}/jobs?content=true"
    try:
        resp = requests.get(url, timeout=15)
        if resp.status_code == 404:
            logger.warning("greenhouse: board %r returned 404 — drop from list", slug)
            return
        resp.raise_for_status()
        data = resp.json()
    except Exception as exc:
        logger.warning("greenhouse: fetch failed for %r: %s", slug, exc)
        return

    raw_count = 0
    yielded = 0
    skipped_loc = 0
    for job in data.get("jobs", []):
        raw_count += 1
        title = job.get("title", "")
        location = job.get("location", {}).get("name", "Unknown")
        description = _strip_html(job.get("content", ""))
        link = job.get("absolute_url", "")

        if not _matches(f"{title} {description}"):
            continue

        if location_filter_enabled() and not is_local_or_remote(location):
            skipped_loc += 1
            continue

        yielded += 1
        yield {
            "id": make_job_id(link, title, display_name),
            "source": "greenhouse",
            "query": "",
            "title": title,
            "company": display_name,
            "location": location,
            "description": description[:3000],  # cap long descriptions
            "url": link,
        }
    logger.info("greenhouse: %s yielded=%d (raw=%d, location-filtered=%d)",
                slug, yielded, raw_count, skipped_loc)


def _fetch_lever(slug: str, display_name: str):
    """Fetch open roles from a Lever board."""
    url = f"https://api.lever.co/v0/postings/{slug}"
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

    raw_count = 0
    yielded = 0
    skipped_loc = 0
    for job in data:
        raw_count += 1
        title = job.get("text", "")
        categories = job.get("categories", {})
        location = categories.get("location", "Unknown")
        # Lever stores description in descriptionPlain or lists
        description = job.get("descriptionPlain", "")
        if not description:
            # Build from additional/lists sections
            parts = []
            for section in job.get("lists", []):
                parts.append(section.get("text", ""))
                parts.append(_strip_html(section.get("content", "")))
            description = "\n".join(parts)

        link = job.get("hostedUrl", "") or job.get("applyUrl", "")

        if not _matches(f"{title} {description}"):
            continue

        if location_filter_enabled() and not is_local_or_remote(location):
            skipped_loc += 1
            continue

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
    logger.info("lever: %s yielded=%d (raw=%d, location-filtered=%d)",
                slug, yielded, raw_count, skipped_loc)


def fetch():
    """Yield job dicts from all tracked Greenhouse and Lever boards."""
    for _, slug, name in GREENHOUSE_COMPANIES:
        yield from _fetch_greenhouse(slug, name)
        time.sleep(0.5)

    for _, slug, name in LEVER_COMPANIES:
        yield from _fetch_lever(slug, name)
        time.sleep(0.5)
