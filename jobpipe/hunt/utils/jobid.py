"""
utils/jobid.py — canonical job IDs for cross-source dedup.

Different sources surface the same role via different URLs (Greenhouse direct
post vs SerpAPI's reshare vs an Indeed scrape). To keep one Supabase row per
real job, we hash on the (canonical_url, normalized_company, normalized_title)
tuple instead of including the source name. The legacy per-source hash lived
inside each source module and double-counted.

If a stable ATS URL is available we strip query strings and hash that;
otherwise we fall back to (company, title) so cross-source matches still
collapse when the URL differs but the role is identifiable.
"""

from __future__ import annotations

import hashlib
import re
from urllib.parse import urlparse, urlunparse

# ── Normalisation helpers ─────────────────────────────────────────────────

_WS_RE = re.compile(r"\s+")
_PUNCT_RE = re.compile(r"[^\w\s]")
_TITLE_NOISE = (
    "remote",
    "us remote",
    "u.s. remote",
    "remote - us",
    "remote-us",
    "(remote)",
    "[remote]",
    "hybrid",
    "(hybrid)",
    "[hybrid]",
    "full time",
    "full-time",
    "(full-time)",
    "(full time)",
    "[hiring]",
)


def _normalise_text(text: str) -> str:
    text = (text or "").lower()
    for noise in _TITLE_NOISE:
        text = text.replace(noise, " ")
    text = _PUNCT_RE.sub(" ", text)
    text = _WS_RE.sub(" ", text).strip()
    return text


# Hosts that resolve to the same posting under different subdomains. Mapped
# to a canonical name so cross-source dedup catches them even when the source
# URL differs only in subdomain.
_HOST_ALIASES = {
    "boards.greenhouse.io": "job-boards.greenhouse.io",
    "boards.eu.greenhouse.io": "job-boards.greenhouse.io",
}


def canonical_url(url: str) -> str:
    """Return a URL with the query string, trailing slash, and host aliases
    normalised.

    Many ATS / aggregator URLs add tracking params (utm_*, gh_jid, src=...)
    that change between sources but point at the same posting. Greenhouse in
    particular serves the same job under both ``boards.greenhouse.io`` and
    ``job-boards.greenhouse.io`` — see ``_HOST_ALIASES``.
    """
    if not url:
        return ""
    try:
        parsed = urlparse(url)
        host = (parsed.netloc or "").lower()
        host = _HOST_ALIASES.get(host, host)
        path = parsed.path.rstrip("/") if parsed.path else ""
        # Drop query + fragment entirely. We've never needed query params to
        # disambiguate two genuinely-different roles.
        canonical = urlunparse((parsed.scheme.lower(),
                                host,
                                path,
                                "",
                                "",
                                ""))
        return canonical
    except Exception:
        return (url or "").lower().split("?", 1)[0].rstrip("/")


def make_job_id(url: str, title: str, company: str) -> str:
    """Stable 16-char hex id for a (url, title, company) tuple, source-agnostic.

    Hashing both the canonical URL and the normalised text means we still
    collapse duplicates when the URL is missing or differs slightly (e.g.,
    ``boards.greenhouse.io`` vs ``job-boards.greenhouse.io``).
    """
    parts = [
        canonical_url(url),
        _normalise_text(company),
        _normalise_text(title),
    ]
    fingerprint = "|".join(parts)
    return hashlib.sha1(fingerprint.encode("utf-8")).hexdigest()[:16]
