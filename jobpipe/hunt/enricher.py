"""Description enricher — fetches full job posting text when sources provide sparse summaries.

Many sources (Indeed RSS, some SerpAPI results) return short marketing
blurbs instead of the actual job description. This module follows the
job URL and attempts to extract the real posting text, giving the scorer
much better signal.

Flattened from ``utils/enricher.py`` in PR-3. The local ``_strip_html`` /
``WHITESPACE_RE`` pair was promoted to ``jobpipe.shared.html`` as
``clean_html_to_text``.

Usage:
    enriched = enrich_description(job)
    # Returns the original job dict with description replaced if a
    # richer version was found, or unchanged if not.
"""

from __future__ import annotations

import re

import requests

from jobpipe.shared.html import clean_html_to_text

# Minimum description length (chars) before we consider enriching.
# Descriptions shorter than this are likely boilerplate.
MIN_DESCRIPTION_LEN = 200

# Maximum description length to store (avoid multi-page HTML dumps).
MAX_DESCRIPTION_LEN = 5000

# Timeout for fetching the full page.
FETCH_TIMEOUT = 15

# Common selectors where job descriptions live, as simple substring
# markers in the raw HTML. We look for content between these tags.
# This is a lightweight approach that avoids needing BeautifulSoup.
DESCRIPTION_MARKERS = [
    # Indeed
    ("id=\"jobDescriptionText\"", "</div>"),
    # LinkedIn
    ("class=\"description__text", "</div>"),
    ("class=\"show-more-less-html__markup", "</div>"),
    # Greenhouse
    ("id=\"content\"", "</div>"),
    # Lever
    ("class=\"posting-categories", "class=\"posting-btn-submit"),
    # Generic job posting patterns
    ("class=\"job-description\"", "</div>"),
    ("class=\"jobDescription\"", "</div>"),
    ("class=\"job_description\"", "</div>"),
    ("class=\"posting-description\"", "</div>"),
    ("class=\"job-details\"", "</div>"),
]

# User-agent to avoid being blocked by job sites.
HEADERS = {
    "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
                  "AppleWebKit/537.36 (KHTML, like Gecko) "
                  "Chrome/120.0.0.0 Safari/537.36",
}


def _extract_description(html: str) -> str | None:
    """Try to pull the job description text from raw HTML.

    Uses marker-based extraction — finds the first matching marker pair
    and extracts everything between them. Falls back to a simple
    heuristic: find the longest text block in the page.
    """
    # Try marker-based extraction
    for start_marker, end_marker in DESCRIPTION_MARKERS:
        idx = html.find(start_marker)
        if idx == -1:
            continue
        # Find the content after the marker's tag
        tag_end = html.find(">", idx)
        if tag_end == -1:
            continue
        content_start = tag_end + 1
        # Find closing marker
        end_idx = html.find(end_marker, content_start)
        if end_idx == -1:
            # Take a generous chunk
            end_idx = min(content_start + MAX_DESCRIPTION_LEN * 2, len(html))
        chunk = html[content_start:end_idx]
        text = clean_html_to_text(chunk)
        if len(text) >= MIN_DESCRIPTION_LEN:
            return text[:MAX_DESCRIPTION_LEN]

    # Fallback: find the longest paragraph-like block
    # Split on common section boundaries and find the longest text section
    paragraphs = re.split(r"<(?:div|section|article)[^>]*>", html)
    best = ""
    for p in paragraphs:
        text = clean_html_to_text(p)
        if len(text) > len(best):
            best = text
    if len(best) >= MIN_DESCRIPTION_LEN:
        return best[:MAX_DESCRIPTION_LEN]

    return None


def enrich_description(job: dict) -> dict:
    """Enrich a job's description if it's too short.

    Returns a new dict with the description replaced if enrichment
    succeeded, or the original dict unchanged.
    """
    description = job.get("description", "")
    url = job.get("url", "")

    # Already have a decent description — skip
    if len(description) >= MIN_DESCRIPTION_LEN:
        return job

    # No URL to fetch — can't enrich
    if not url:
        return job

    try:
        resp = requests.get(url, headers=HEADERS, timeout=FETCH_TIMEOUT,
                            allow_redirects=True)
        if resp.status_code != 200:
            return job
        html = resp.text
    except requests.RequestException:
        return job

    enriched = _extract_description(html)
    if enriched and len(enriched) > len(description):
        return {**job, "description": enriched}

    return job
