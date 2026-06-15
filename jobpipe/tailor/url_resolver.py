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

import json
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
    # High-frequency aggregators in the funnel (feat/hunt-resolver-aggregator).
    # Added so they enter the DOM-extraction path below; most embed a real
    # ATS apply link the strengthened extractor can recover.
    "simplify.jobs",
    "tealhq.com",
    "wellfound.com",
    "talent.com",
    "jooble.org",
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


def is_ats_url(url: str) -> bool:
    """True when ``url``'s host is already a known direct-ATS host.

    The hunt discovery gate calls this to short-circuit clean direct-ATS
    sources (greenhouse / lever / ashby / workday) — they need no resolver
    fetch, so surfacing them costs zero extra HTTP."""
    return _is_ats(_host_of(url))


def _is_aggregator(host: str) -> bool:
    return host in AGGREGATOR_HOSTS or any(host.endswith("." + a) for a in AGGREGATOR_HOSTS)


# A direct ATS URL embedded anywhere in a string of script/JSON text. Used by
# the JSON-LD and embedded-app-JSON strategies, where the apply link is a value
# inside a blob too large/irregular to JSON-parse reliably.
_ATS_URL_IN_TEXT = re.compile(
    r"https?://[^\s\"'<>\\)]*(?:" + "|".join(re.escape(h) for h in KNOWN_ATS_HOSTS) + r")[^\s\"'<>\\)]*",
    re.IGNORECASE,
)


def _first_ats_url_in_text(text: str) -> str | None:
    """First substring in ``text`` that is a URL on a known ATS host.

    JSON-encoded values may carry escaped slashes (``https:\\/\\/``); unescape
    them so the match is a usable URL. Returns None when nothing matches."""
    if not text:
        return None
    m = _ATS_URL_IN_TEXT.search(text.replace("\\/", "/"))
    if not m:
        return None
    url = m.group(0)
    return url if _is_ats(_host_of(url)) else None


def _iter_jsonld_strings(obj):
    """Yield every string value reachable in a parsed JSON-LD object/array."""
    if isinstance(obj, dict):
        for v in obj.values():
            yield from _iter_jsonld_strings(v)
    elif isinstance(obj, list):
        for v in obj:
            yield from _iter_jsonld_strings(v)
    elif isinstance(obj, str):
        yield obj


def _from_jsonld(soup: BeautifulSoup) -> str | None:
    """Strategy 1: a schema.org JobPosting's apply URL.

    Parses every ``<script type="application/ld+json">`` block and returns the
    first string value anywhere inside it that is a known-ATS URL — covering
    ``url``, ``sameAs``, and apply-action targets without hard-coding the path.
    """
    for tag in soup.find_all("script", attrs={"type": "application/ld+json"}):
        raw = tag.string or tag.get_text() or ""
        if not raw.strip():
            continue
        try:
            data = json.loads(raw)
        except (ValueError, TypeError):
            # Tolerate a junk block — scan its raw text instead of giving up.
            hit = _first_ats_url_in_text(raw)
            if hit:
                logger.info(f"resolver: ATS link via JSON-LD (raw) → {hit}")
                return hit
            continue
        for s in _iter_jsonld_strings(data):
            if s.startswith("http") and _is_ats(_host_of(s)):
                logger.info(f"resolver: ATS link via JSON-LD → {s}")
                return s
    return None


def _from_embedded_json(soup: BeautifulSoup) -> str | None:
    """Strategy 2: a direct ATS URL embedded in an app-state script.

    Next.js (``__NEXT_DATA__``), React Query, and similar hydration blobs carry
    the canonical apply URL as a JSON value. The blobs are large and irregular,
    so scan their raw text for the first known-ATS URL rather than parsing.
    """
    for tag in soup.find_all("script"):
        stype = (tag.get("type") or "").lower()
        # Skip JSON-LD (handled above) and non-JS data islands we don't read.
        if stype == "application/ld+json":
            continue
        text = tag.string or tag.get_text() or ""
        hit = _first_ats_url_in_text(text)
        if hit:
            logger.info(f"resolver: ATS link via embedded JSON → {hit}")
            return hit
    return None


_ATS_DATA_ATTRS = ("data-apply-url", "data-href", "data-url", "data-redirect", "data-link")


def _from_anchors(soup: BeautifulSoup, base_url: str) -> str | None:
    """Strategy 3: an anchor/button href or data-* attr pointing at a known ATS."""
    for tag in soup.find_all(["a", "button"]):
        candidates = []
        if tag.has_attr("href"):
            candidates.append(tag["href"])
        for attr in _ATS_DATA_ATTRS:
            if tag.has_attr(attr):
                candidates.append(tag[attr])
        for raw in candidates:
            full = urljoin(base_url, raw)
            if _is_ats(_host_of(full)):
                logger.info(f"resolver: ATS link via anchor/data-attr → {full}")
                return full
    return None


def _one_hop_final_url(url: str, timeout: float = 10.0) -> str | None:
    """Follow ``url`` through redirects with a single bounded GET; return the
    final URL (or None on any failure). Isolated so tests can patch it without
    a network round-trip."""
    try:
        with httpx.Client(
            follow_redirects=True,
            timeout=timeout,
            headers={"User-Agent": _USER_AGENT},
        ) as client:
            r = client.get(url)
            return str(r.url)
    except Exception as e:  # noqa: BLE001 — any fetch failure is a clean miss
        logger.info(f"resolver: one-hop redirect fetch failed for {url}: {e}")
        return None


def _from_apply_redirect(soup: BeautifulSoup, base_url: str) -> str | None:
    """Strategy 4: an Apply link to an off-site redirector that lands on an ATS.

    For an anchor whose text reads "apply" and whose href leaves the aggregator
    host (but is not itself an ATS), do ONE bounded redirect-following GET and
    accept the final URL only when its host is a known ATS."""
    base_host = _host_of(base_url)
    for a in soup.find_all("a", href=True):
        text = (a.get_text() or "").strip()
        if not text or not _APPLY_LINK_PATTERNS.search(text):
            continue
        full = urljoin(base_url, a["href"])
        host = _host_of(full)
        if not host or host == base_host or _is_ats(host):
            # same-site (on-site apply) or already-ATS handled by other passes
            continue
        final = _one_hop_final_url(full)
        if final and _is_ats(_host_of(final)):
            logger.info(f"resolver: ATS link via one-hop redirect → {final}")
            return final
    return None


def _extract_ats_link_from_html(
    html: str, base_url: str, *, allow_one_hop: bool = True
) -> str | None:
    """Return the first direct ATS URL discoverable in an aggregator page.

    Tries, in order: schema.org JSON-LD, embedded app-state JSON, anchor/data-*
    attributes pointing at an ATS, and (when ``allow_one_hop``) a single bounded
    redirect-follow on an off-site Apply link. Any parse/fetch failure yields
    None — the caller falls back to the ``aggregator_unverified`` flag. Never
    raises; never fabricates a link.
    """
    try:
        soup = BeautifulSoup(html or "", "html.parser")
    except Exception as e:  # noqa: BLE001 — malformed input must not crash resolve
        logger.info(f"resolver: HTML parse failed: {e}")
        return None

    for strategy in (_from_jsonld, _from_embedded_json):
        hit = strategy(soup)
        if hit:
            return hit
    hit = _from_anchors(soup, base_url)
    if hit:
        return hit
    if allow_one_hop:
        hit = _from_apply_redirect(soup, base_url)
        if hit:
            return hit
    return None


def resolve_application_url(url: str, timeout: float = 15.0) -> dict:
    """
    Return a dict with the resolved URL and a trail of redirects/extractions.

    {
      "original": "...",
      "resolved": "...",         # best guess at the real ATS URL
      "is_ats": True/False,      # whether resolved is a known ATS
      "trail": [url1, url2, ...]
      "notes": "...",
      "status_code": 200/None,   # HTTP status of the fetched page
      "html": "...",             # body of the fetched page (None on error)
    }

    ``status_code`` + ``html`` are carried so a caller can run a liveness
    check on the page WITHOUT re-fetching it (the hunt discovery gate shares
    this one fetch across resolve → liveness → enrich). They reflect the
    aggregator page when an ATS link was extracted from it, and the final
    redirect target otherwise.
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
                    "status_code": r.status_code,
                    "html": r.text,
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
                        "status_code": r.status_code,
                        "html": r.text,
                    }
                notes.append(f"aggregator {final_host}: no ATS link found on page")

            # Fall back to whatever we ended at
            return {
                "original": url,
                "resolved": final_url,
                "is_ats": _is_ats(final_host),
                "trail": trail,
                "notes": "; ".join(notes) or f"final host={final_host}",
                "status_code": r.status_code,
                "html": r.text,
            }
    except Exception as e:
        logger.warning(f"resolver error on {url}: {e}")
        return {
            "original": url,
            "resolved": url,
            "is_ats": False,
            "trail": trail,
            "notes": f"error: {e}",
            "status_code": None,
            "html": None,
        }
