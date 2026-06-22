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
import os
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


# ── Source-URL extraction (off-host canonical/apply URL, possibly non-ATS) ───
# Distinct from the ATS extractor above: this recovers the original *posting*
# URL even when it is NOT (yet) on a known ATS host, so resolve_application_url
# can recurse on it — e.g. TealHQ embeds careers.qualcomm.com, which itself
# leads to the real Workday/Greenhouse form a second hop away. Aggregators
# carry this URL in their JSON-LD JobPosting or SPA hydration blob.

# Keys whose value is the canonical/external apply URL, most specific first.
# ``url`` is last because it is the most ambiguous (also used for logos, the
# company homepage, etc.) — only consulted when nothing more specific matched.
_APPLY_URL_KEYS = (
    "applyurl", "apply_url", "applylink", "apply_link",
    "applicationurl", "application_url",
    "externalurl", "external_url", "externalapplyurl", "external_apply_url",
    "sourceurl", "source_url", "redirecturl", "redirect_url",
    "joburl", "job_url", "url",
)

# Per-aggregator hints: the key under which each host stores the source/apply
# URL, tried before the generic key order. tealhq is verified from a real
# __NEXT_DATA__/__REACT_QUERY_STATE__ capture; simplify/wellfound hints are
# best-effort (their live pages refuse our HTTP client) and fall back to the
# generic walk, which covers them too.
_AGGREGATOR_SOURCE_KEYS = {
    "tealhq.com": ("url",),
    "www.tealhq.com": ("url",),
    "simplify.jobs": ("applyurl", "apply_url", "url"),
    "wellfound.com": ("applyurl", "apply_url", "joburl", "url"),
}

# SPA bootstrap globals whose right-hand side is a JSON object literal.
_SPA_STATE_GLOBALS = (
    "__INITIAL_STATE__", "__NUXT__", "__REACT_QUERY_STATE__",
    "__APOLLO_STATE__", "__PRELOADED_STATE__",
)

_ASSET_EXTS = (
    ".png", ".jpg", ".jpeg", ".gif", ".svg", ".webp", ".ico",
    ".css", ".js", ".woff", ".woff2", ".ttf", ".pdf",
)


def _safe_json(text: str):
    """json.loads that returns None instead of raising on any bad input."""
    try:
        return json.loads(text)
    except (ValueError, TypeError):
        return None


def _balanced_braces(text: str, start: int) -> str | None:
    """Return the ``{...}`` substring of ``text`` starting at ``start`` (a ``{``),
    matching nested braces while respecting JSON string literals. None if
    unbalanced. Used to carve a SPA-state object out of a ``window.X = {…};``
    assignment without depending on a brittle non-greedy regex."""
    depth = 0
    in_str = False
    esc = False
    for i in range(start, len(text)):
        c = text[i]
        if in_str:
            if esc:
                esc = False
            elif c == "\\":
                esc = True
            elif c == '"':
                in_str = False
            continue
        if c == '"':
            in_str = True
        elif c == "{":
            depth += 1
        elif c == "}":
            depth -= 1
            if depth == 0:
                return text[start:i + 1]
    return None


def _iter_spa_json(soup: BeautifulSoup):
    """Yield parsed JSON objects from SPA bootstrap scripts.

    Handles ``<script id="__NEXT_DATA__" type="application/json">`` (pure JSON
    body) and ``window.__INITIAL_STATE__ = {…};``-style assignments. A block
    that won't parse is skipped, never raised."""
    for tag in soup.find_all("script"):
        raw = tag.string or tag.get_text() or ""
        if not raw.strip():
            continue
        if (tag.get("id") or "").strip() == "__NEXT_DATA__":
            obj = _safe_json(raw)
            if obj is not None:
                yield obj
            continue
        for g in _SPA_STATE_GLOBALS:
            idx = raw.find(g)
            if idx == -1:
                continue
            brace = raw.find("{", idx)
            if brace == -1:
                continue
            blob = _balanced_braces(raw, brace)
            obj = _safe_json(blob) if blob else None
            if obj is not None:
                yield obj
            break


def _iter_url_candidates(obj):
    """Walk a parsed JSON object, yielding ``(key_lower, value)`` for every
    string value whose key names an apply/source URL (see ``_APPLY_URL_KEYS``)."""
    if isinstance(obj, dict):
        for k, v in obj.items():
            if (
                isinstance(k, str)
                and isinstance(v, str)
                and k.lower() in _APPLY_URL_KEYS
                and v.startswith("http")
            ):
                yield k.lower(), v
            else:
                yield from _iter_url_candidates(v)
    elif isinstance(obj, list):
        for v in obj:
            yield from _iter_url_candidates(v)


def _iter_dicts(obj):
    """Yield every dict reachable in a parsed JSON object/array."""
    if isinstance(obj, dict):
        yield obj
        for v in obj.values():
            yield from _iter_dicts(v)
    elif isinstance(obj, list):
        for v in obj:
            yield from _iter_dicts(v)


def _is_asset_url(url: str) -> bool:
    try:
        return urlparse(url).path.lower().endswith(_ASSET_EXTS)
    except Exception:  # noqa: BLE001
        return False


def _usable_source(url: str, base_host: str) -> bool:
    """A candidate is a usable next hop only if it is a real off-host page —
    not the aggregator's own host and not a static asset."""
    u = url.replace("\\/", "/")
    host = _host_of(u)
    return bool(host) and host != base_host and not _is_asset_url(u)


def _jsonld_source_url(soup: BeautifulSoup, base_host: str) -> str | None:
    """A schema.org JobPosting's ``url`` / ``sameAs`` pointing off-host.

    Returns the canonical posting URL regardless of whether it is an ATS host
    (the recursion decides what to do with it). Prefers a value found on a
    block actually typed ``JobPosting`` over an incidental ``url`` elsewhere."""
    fallback = None
    for tag in soup.find_all("script", attrs={"type": "application/ld+json"}):
        data = _safe_json(tag.string or tag.get_text() or "")
        if data is None:
            continue
        for d in _iter_dicts(data):
            t = d.get("@type") or d.get("type")
            is_job = (isinstance(t, str) and "jobposting" in t.lower()) or (
                isinstance(t, list) and any("jobposting" in str(x).lower() for x in t)
            )
            for key in ("url", "sameAs"):
                val = d.get(key)
                cand = None
                if isinstance(val, str):
                    cand = val
                elif isinstance(val, list):
                    cand = next(
                        (x for x in val if isinstance(x, str) and x.startswith("http")),
                        None,
                    )
                if cand and cand.startswith("http") and _usable_source(cand, base_host):
                    if is_job:
                        return cand.replace("\\/", "/")
                    fallback = fallback or cand.replace("\\/", "/")
    return fallback


def _best_source_candidate(candidates, base_host: str, preferred_keys=()) -> str | None:
    """Pick the best off-host source URL from ``(key, url)`` SPA candidates.

    Order: an aggregator-specific preferred key → any known-ATS host → any
    apply-specific key → the first generic ``url``."""
    cleaned = [
        (key, url.replace("\\/", "/"))
        for key, url in candidates
        if _usable_source(url, base_host)
    ]
    if not cleaned:
        return None
    for pk in preferred_keys:
        for key, u in cleaned:
            if key == pk:
                return u
    for _, u in cleaned:
        if _is_ats(_host_of(u)):
            return u
    for key, u in cleaned:
        if key != "url":
            return u
    return cleaned[0][1]


def _extract_source_url_from_html(html: str, base_url: str) -> str | None:
    """Return the canonical *source* posting URL embedded in an aggregator page,
    even when it is not (yet) on a known ATS host — so ``resolve_application_url``
    can recurse on it. Tries JSON-LD JobPosting (``url`` / ``sameAs``) then SPA
    bootstrap state (``__NEXT_DATA__``, ``window.__INITIAL_STATE__`` / ``__NUXT__``
    / ``__REACT_QUERY_STATE__``, …), honouring per-aggregator key hints. Excludes
    same-host self-links and static assets. Never raises; never fabricates."""
    try:
        soup = BeautifulSoup(html or "", "html.parser")
    except Exception as e:  # noqa: BLE001 — malformed input must not crash resolve
        logger.info(f"resolver: source HTML parse failed: {e}")
        return None

    base_host = _host_of(base_url)
    hit = _jsonld_source_url(soup, base_host)
    if hit:
        logger.info(f"resolver: source URL via JSON-LD → {hit}")
        return hit

    candidates = []
    for obj in _iter_spa_json(soup):
        candidates.extend(_iter_url_candidates(obj))
    preferred = _AGGREGATOR_SOURCE_KEYS.get(base_host, ())
    hit = _best_source_candidate(candidates, base_host, preferred)
    if hit:
        logger.info(f"resolver: source URL via SPA state → {hit}")
        return hit
    return None


def _fetch_page(url: str, timeout: float = 15.0):
    """Fetch ``url`` following HTTP redirects with one bounded GET.

    Returns ``(final_url, status_code, html, history_urls)``; on any failure
    returns ``(url, None, None, [])`` (a clean miss the caller can fall back
    on). Isolated as the single network seam so the recursion in
    :func:`resolve_application_url` is fully testable without live HTTP."""
    try:
        with httpx.Client(
            follow_redirects=True,
            timeout=timeout,
            headers={"User-Agent": _USER_AGENT},
        ) as client:
            r = client.get(url)
            history = [str(h.url) for h in r.history]
            return str(r.url), r.status_code, r.text, history
    except Exception as e:  # noqa: BLE001 — any fetch failure is a clean miss
        logger.warning(f"resolver fetch error on {url}: {e}")
        return url, None, None, []


def _result(original, resolved, is_ats, trail, notes, status_code, html) -> dict:
    return {
        "original": original,
        "resolved": resolved,
        "is_ats": is_ats,
        "trail": trail,
        "notes": notes,
        "status_code": status_code,
        "html": html,
    }


def resolve_application_url(url: str, timeout: float = 15.0, *, max_hops: int = 3) -> dict:
    """
    Resolve ``url`` to the deepest real ATS application URL reachable, following
    aggregator → aggregator → careers-site → ATS chains via static extraction.

    Returns:
    {
      "original": "...",
      "resolved": "...",         # deepest URL reached (ATS when found)
      "is_ats": True/False,      # whether resolved is a known ATS host
      "trail": [url1, url2, ...], # every redirect + extraction hop
      "notes": "...",
      "status_code": 200/None,   # HTTP status of the FIRST fetched page
      "html": "...",             # body of the FIRST fetched page (None on error)
    }

    Each hop: fetch (following redirects); if the final host is a known ATS,
    stop. Otherwise try to extract a direct ATS link from the page, then a
    canonical *source* URL (which may be another aggregator or a careers
    landing page) and recurse on it — capped at ``max_hops`` and guarded
    against host-revisit loops.

    ``status_code`` + ``html`` reflect the FIRST fetched page so the hunt
    discovery gate can share this one fetch across resolve → liveness →
    enrich without re-fetching (the first page — typically the aggregator —
    carries the richest description).
    """
    original = url
    trail = [url]
    notes: list[str] = []
    first_status = None
    first_html = None
    visited_hosts: set[str] = set()
    current = url
    last_final = url

    for hop in range(max_hops):
        final_url, status_code, html, history = _fetch_page(current, timeout)
        for h in history:
            if h not in trail:
                trail.append(h)
        if final_url not in trail:
            trail.append(final_url)
        if hop == 0:
            first_status, first_html = status_code, html
        last_final = final_url
        final_host = _host_of(final_url)

        # Direct ATS reached (redirect target is itself an ATS) → done.
        if _is_ats(final_host):
            return _result(
                original, final_url, True, trail,
                "direct redirect to ATS" if hop == 0 else
                f"reached ATS after {hop + 1} hops",
                first_status, first_html,
            )

        # Try to recover a direct ATS link embedded in this page.
        ats_url = _extract_ats_link_from_html(html, final_url) if html else None
        if ats_url:
            return _result(
                original, ats_url, _is_ats(_host_of(ats_url)),
                trail + ([ats_url] if ats_url not in trail else []),
                f"extracted ATS link from {final_host}",
                first_status, first_html,
            )

        # Otherwise look for a canonical source URL to follow.
        visited_hosts.add(final_host)
        source_url = _extract_source_url_from_html(html, final_url) if html else None
        if not source_url:
            notes.append(f"{final_host}: no deeper apply link found")
            break
        if _host_of(source_url) in visited_hosts or source_url in trail:
            notes.append(f"loop guard: {source_url} already visited")
            break
        if _is_ats(_host_of(source_url)):
            # Source is already an ATS — accept without spending another fetch.
            return _result(
                original, source_url, True, trail + [source_url],
                f"source URL from {final_host} is ATS",
                first_status, first_html,
            )
        trail.append(source_url)
        current = source_url
    else:
        notes.append(f"hop cap ({max_hops}) reached")

    # Exhausted / dead-ended — return the deepest page we actually reached.
    return _result(
        original, last_final, _is_ats(_host_of(last_final)), trail,
        "; ".join(notes) or f"final host={_host_of(last_final)}",
        first_status, first_html,
    )


# ── Gated headless fallback (the hard, JS-driven Apply flows) ────────────────
# When static extraction can't crack a page (the Apply action is pure JS with
# no embedded source URL — TealHQ's motivating case), drive a real browser
# through the Apply flow and capture the final ATS URL. EXPENSIVE + network, so
# it is OPT-IN: the cron hunt only invokes it when ``JOBPIPE_RESOLVE_HEADLESS``
# is set, and only for results still flagged ``aggregator_unverified``. The
# tailor's prepare step may also call ``resolve_to_ats_headless`` on-demand for
# a single job. Playwright is imported lazily so the static path keeps zero
# extra dependencies at import time.

_RESOLVE_HEADLESS_ENV = "JOBPIPE_RESOLVE_HEADLESS"

# Anchors/buttons whose text/role indicates the Apply action to click.
_APPLY_CLICK_TEXTS = ("apply now", "apply for this job", "apply", "i'm interested")


def resolve_headless_enabled() -> bool:
    """True when ``JOBPIPE_RESOLVE_HEADLESS`` is truthy.

    Read live (not captured at import) so a process or test can toggle it.
    Default OFF — the cron hunt must stay cheap; the Playwright fallback is
    strictly opt-in."""
    return os.environ.get(_RESOLVE_HEADLESS_ENV, "").strip().lower() in (
        "1", "true", "yes", "on",
    )


def _click_apply(page, context, timeout_ms: int) -> "object | None":
    """Click the first Apply-looking control on ``page``.

    Returns the page that holds the result of the click: a freshly opened tab
    if the Apply opened a popup/new window, otherwise the same page after its
    navigation settles. Returns None when no Apply control was found/clickable.
    """
    for text in _APPLY_CLICK_TEXTS:
        locator = page.get_by_role("link", name=text, exact=False).first
        try:
            count = locator.count()
        except Exception:  # noqa: BLE001
            count = 0
        if not count:
            locator = page.get_by_role("button", name=text, exact=False).first
            try:
                count = locator.count()
            except Exception:  # noqa: BLE001
                count = 0
        if not count:
            continue
        # The Apply action may open a new tab; race a popup against same-tab nav.
        try:
            with context.expect_page(timeout=timeout_ms) as popup_info:
                locator.click(timeout=timeout_ms)
            new_page = popup_info.value
            new_page.wait_for_load_state("domcontentloaded", timeout=timeout_ms)
            return new_page
        except Exception:  # noqa: BLE001 — no popup; treat as same-tab navigation
            try:
                page.wait_for_load_state("domcontentloaded", timeout=timeout_ms)
            except Exception:  # noqa: BLE001
                pass
            return page
    return None


def _headless_driver(url: str, *, max_clicks: int = 3, nav_timeout_ms: int = 20000):
    """Drive a headless browser from ``url`` through up to ``max_clicks`` Apply
    actions, returning the final landed URL (str) or None.

    Reuses the submit browser infra (cookieless headless context). The caller
    (:func:`resolve_to_ats_headless`) decides whether the landed URL is an ATS.
    Best-effort: any Playwright error propagates to the caller, which logs and
    returns None. Verified manually against the TealHQ → Qualcomm flow."""
    from playwright.sync_api import sync_playwright  # lazy: keep import-time light

    from jobpipe.submit.browser import local as _local

    with sync_playwright() as pw:
        context, closer = _local.open_browser_context(pw, headless=True)
        try:
            page = context.new_page()
            page.goto(url, wait_until="domcontentloaded", timeout=nav_timeout_ms)
            for _ in range(max_clicks):
                if _is_ats(_host_of(page.url)):
                    return page.url
                landed = _click_apply(page, context, nav_timeout_ms)
                if landed is None:
                    break
                page = landed
            return page.url
        finally:
            closer()


def resolve_to_ats_headless(url: str, *, max_clicks: int = 3):
    """Drive a real headless browser through the Apply flow to capture the final
    ATS application URL. Returns that URL (str) when a known ATS host is reached,
    else None.

    EXPENSIVE + network. Gate the call behind :func:`resolve_headless_enabled`
    in the cron hunt; the tailor may call it on-demand for a single job. Never
    raises — a missing browser / navigation failure logs and returns None."""
    try:
        final = _headless_driver(url, max_clicks=max_clicks)
    except Exception as e:  # noqa: BLE001 — headless is best-effort, never fatal
        logger.warning(f"resolver: headless resolve failed for {url}: {e}")
        return None
    if final and _is_ats(_host_of(final)):
        logger.info(f"resolver: headless resolved {url} → {final}")
        return final
    logger.info(f"resolver: headless reached non-ATS {final} for {url}")
    return None
