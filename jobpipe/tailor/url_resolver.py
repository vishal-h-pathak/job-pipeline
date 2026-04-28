"""
url_resolver.py — Follow aggregator redirects to the real ATS URL.

Many job-hunter sources produce aggregator URLs (Remotive, WeWorkRemotely,
careervault.io, learn4good.com, whatjobs.com) that wrap the real ATS (Greenhouse,
Lever, Ashby, Workday, etc.). This module:

  1. Follows HTTP redirects.
  2. If the final host is a known aggregator, fetches the page and extracts the
     canonical ATS "Apply" link via DOM heuristics.
  3. Returns the ATS URL if found, else the original URL (so the agent can still
     try to drive the aggregator page).

Keep it dependency-light: httpx + BeautifulSoup.
"""

from __future__ import annotations

import logging
import re
from urllib.parse import urlparse, urljoin

import httpx
from bs4 import BeautifulSoup

logger = logging.getLogger("tailor.url_resolver")

# Hosts that wrap ATSes
AGGREGATOR_HOSTS = {
    "remotive.com",
    "remotive.io",
    "weworkremotely.com",
    "careervault.io",
    "learn4good.com",
    "whatjobs.com",
    "jobs.remotive.com",
}

# Known final-destination ATS hosts (if we hit these after redirects, stop)
KNOWN_ATS_HOSTS = (
    "greenhouse.io",
    "boards.greenhouse.io",
    "job-boards.greenhouse.io",
    "lever.co",
    "jobs.lever.co",
    "ashbyhq.com",
    "jobs.ashbyhq.com",
    "workday.com",
    "myworkdayjobs.com",
    "icims.com",
    "smartrecruiters.com",
    "workable.com",
    "bamboohr.com",
)

_APPLY_LINK_PATTERNS = re.compile(
    r"(apply|application|apply for|apply now|apply here)", re.IGNORECASE
)

_USER_AGENT = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 14_0) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/120.0 Safari/537.36"
)


def _host_of(url: str) -> str:
    try:
        return (urlparse(url).hostname or "").lower()
    except Exception:
        return ""


def _is_ats(host: str) -> bool:
    return any(ats in host for ats in KNOWN_ATS_HOSTS)


def _is_aggregator(host: str) -> bool:
    return host in AGGREGATOR_HOSTS or any(host.endswith("." + a) for a in AGGREGATOR_HOSTS)


def _extract_ats_link_from_html(html: str, base_url: str) -> str | None:
    soup = BeautifulSoup(html, "html.parser")
    # First pass: any anchor whose href points directly at a known ATS host
    for a in soup.find_all("a", href=True):
        href = a["href"]
        full = urljoin(base_url, href)
        host = _host_of(full)
        if _is_ats(host):
            logger.info(f"resolver: ATS link found via href → {full}")
            return full
    # Second pass: anchor with "Apply" text whose href goes anywhere external
    base_host = _host_of(base_url)
    for a in soup.find_all("a", href=True):
        text = (a.get_text() or "").strip()
        if not text:
            continue
        if _APPLY_LINK_PATTERNS.search(text):
            full = urljoin(base_url, a["href"])
            host = _host_of(full)
            if host and host != base_host:
                logger.info(f"resolver: Apply-text link → {full}")
                return full
    return None


def resolve_application_url(url: str, timeout: float = 15.0) -> dict:
    """
    Return a dict with the resolved URL and a trail of redirects/extractions.

    {
      "original": "...",
      "resolved": "...",         # best guess at the real ATS URL
      "is_ats": True/False,      # whether resolved is a known ATS
      "trail": [url1, url2, ...]
      "notes": "..."
    }
    """
    trail = [url]
    notes = []
    try:
        with httpx.Client(
            follow_redirects=True,
            timeout=timeout,
            headers={"User-Agent": _USER_AGENT},
        ) as client:
            r = client.get(url)
            # Record history
            for h in r.history:
                trail.append(str(h.url))
            trail.append(str(r.url))

            final_url = str(r.url)
            final_host = _host_of(final_url)

            if _is_ats(final_host):
                return {
                    "original": url,
                    "resolved": final_url,
                    "is_ats": True,
                    "trail": trail,
                    "notes": "direct redirect to ATS",
                }

            if _is_aggregator(final_host):
                # Try to extract the real ATS URL from the aggregator page
                ats_url = _extract_ats_link_from_html(r.text, final_url)
                if ats_url:
                    return {
                        "original": url,
                        "resolved": ats_url,
                        "is_ats": _is_ats(_host_of(ats_url)),
                        "trail": trail + [ats_url],
                        "notes": f"extracted from aggregator ({final_host})",
                    }
                notes.append(f"aggregator {final_host}: no ATS link found on page")

            # Fall back to whatever we ended at
            return {
                "original": url,
                "resolved": final_url,
                "is_ats": _is_ats(final_host),
                "trail": trail,
                "notes": "; ".join(notes) or f"final host={final_host}",
            }
    except Exception as e:
        logger.warning(f"resolver error on {url}: {e}")
        return {
            "original": url,
            "resolved": url,
            "is_ats": False,
            "trail": trail,
            "notes": f"error: {e}",
        }
