"""Generic Playwright-driven fallback scraper for the manual-tailor flow.

Two-layer design:

* :func:`parse_jobposting_html` is a pure function — takes rendered
  HTML + the URL, returns a ScrapedPosting. Unit-testable with saved
  HTML fixtures. Tries JSON-LD ``JobPosting`` first (the cleanest
  signal when a posting page implements schema.org), then falls back
  to ``og:*`` / ``<title>`` / ``<h1>`` heuristics.

* :func:`fetch_generic_posting` is the IO wrapper — launches
  Playwright (headless Chromium), navigates to the URL, waits for the
  body, hands the rendered HTML to the parser. Covered by step ⑥
  live verification rather than unit tests; the parser carries the
  semantic load.

Always returns ``confidence='low'`` — even a clean JSON-LD parse
goes through the dashboard review surface per Amendment 1, because
we have no way to verify the org actually owns the role (some
aggregators republish JobPosting JSON-LD pointing at someone else's
ATS).
"""

from __future__ import annotations

import json
import logging
import re

from bs4 import BeautifulSoup

from jobpipe.shared.html import strip_tags
from jobpipe.shared.jobid import canonical_url

from . import ScrapedPosting, ScrapeError

logger = logging.getLogger("tailor.manual.generic")


def _flatten_jsonld(blob) -> list[dict]:
    """Yield every JobPosting-shaped dict in a JSON-LD blob.

    JSON-LD can be a single object, an array, or an @graph wrapper.
    """
    out: list[dict] = []

    def walk(node):
        if isinstance(node, dict):
            t = node.get("@type")
            if t == "JobPosting" or (isinstance(t, list) and "JobPosting" in t):
                out.append(node)
            for v in node.values():
                walk(v)
        elif isinstance(node, list):
            for item in node:
                walk(item)

    walk(blob)
    return out


def _compose_location(loc) -> str | None:
    """Flatten schema.org PostalAddress / Place into a single string."""
    if not loc:
        return None
    if isinstance(loc, list):
        loc = loc[0] if loc else None
    if not isinstance(loc, dict):
        return str(loc).strip() or None

    addr = loc.get("address") or loc
    if isinstance(addr, dict):
        parts = [
            (addr.get("addressLocality") or "").strip(),
            (addr.get("addressRegion") or "").strip(),
            (addr.get("addressCountry") or "").strip(),
        ]
        joined = ", ".join(p for p in parts if p)
        return joined or None
    return None


def _parse_via_jsonld(soup: BeautifulSoup) -> dict | None:
    """Return a partial posting dict from any JSON-LD JobPosting block."""
    for tag in soup.find_all("script", attrs={"type": "application/ld+json"}):
        raw = (tag.string or tag.get_text() or "").strip()
        if not raw:
            continue
        try:
            blob = json.loads(raw)
        except json.JSONDecodeError:
            continue
        candidates = _flatten_jsonld(blob)
        if not candidates:
            continue
        posting = candidates[0]
        title = (posting.get("title") or "").strip()
        if not title:
            continue
        desc_raw = posting.get("description") or ""
        desc = strip_tags(desc_raw) if "<" in desc_raw else desc_raw.strip()
        org = posting.get("hiringOrganization") or {}
        company = (
            (org.get("name") if isinstance(org, dict) else None) or ""
        ).strip() or None
        location = _compose_location(posting.get("jobLocation"))
        return {
            "title": title,
            "description": desc,
            "company": company,
            "location": location,
        }
    return None


def _meta(soup: BeautifulSoup, prop: str) -> str:
    tag = soup.find("meta", attrs={"property": prop}) or soup.find(
        "meta", attrs={"name": prop}
    )
    if tag and tag.get("content"):
        return tag["content"].strip()
    return ""


def _parse_via_heuristics(soup: BeautifulSoup) -> dict:
    """Last-resort field extraction from og:* / <title> / <h1>."""
    title = (
        _meta(soup, "og:title")
        or (soup.title.string.strip() if soup.title and soup.title.string else "")
        or (soup.h1.get_text().strip() if soup.h1 else "")
    )
    description = _meta(soup, "og:description") or _meta(soup, "description")
    company = _meta(soup, "og:site_name")
    # Strip " — Company" / " - Company" suffix from title if it duplicates
    # the og:site_name field.
    if title and company:
        for sep in (" — ", " - ", " | "):
            tail = f"{sep}{company}"
            if title.endswith(tail):
                title = title[: -len(tail)].strip()
                break
    return {
        "title": title.strip(),
        "description": description.strip(),
        "company": company.strip() or None,
        "location": None,
    }


def parse_jobposting_html(html: str, url: str) -> ScrapedPosting:
    """Parse a rendered job-posting HTML page into a ScrapedPosting.

    Strategy:
      1. Walk every ``<script type="application/ld+json">`` for a
         ``JobPosting`` block. Use that if present + has a title.
      2. Else fall back to ``og:*`` / ``<title>`` / ``<h1>`` heuristics.

    Returns ``confidence='low'`` unconditionally — even a clean
    JSON-LD parse routes through the dashboard review surface per
    Amendment 1.

    Raises ScrapeError if neither path can recover a title.
    """
    soup = BeautifulSoup(html, "html.parser")
    fields = _parse_via_jsonld(soup) or _parse_via_heuristics(soup)
    title = fields.get("title") or ""
    if not title:
        raise ScrapeError(
            f"generic scrape: no title found on {url} "
            "(no JSON-LD JobPosting and no og:title / <title> / <h1>)"
        )
    description = fields.get("description") or ""
    # Collapse runs of whitespace introduced by HTML→text.
    description = re.sub(r"\n{3,}", "\n\n", description).strip()
    return ScrapedPosting(
        url=canonical_url(url),
        title=title.strip(),
        company=fields.get("company"),
        location=fields.get("location"),
        description=description,
        ats_kind="generic",
        confidence="low",
    )


def fetch_generic_posting(
    url: str, *, timeout: float = 20.0
) -> ScrapedPosting:
    """Launch headless Chromium, render the URL, and parse the HTML.

    Lazy-imports Playwright so the rest of the manual module stays
    importable in CI environments without a browser installed.
    """
    try:
        from playwright.sync_api import sync_playwright
    except ImportError as exc:
        raise ScrapeError(
            "generic fallback needs playwright; install with "
            "`pip install playwright && playwright install chromium`"
        ) from exc

    try:
        with sync_playwright() as pw:
            browser = pw.chromium.launch(headless=True)
            try:
                ctx = browser.new_context(
                    user_agent=(
                        "Mozilla/5.0 (Macintosh; Intel Mac OS X 14_0) "
                        "AppleWebKit/537.36 (KHTML, like Gecko) "
                        "Chrome/121.0 Safari/537.36"
                    ),
                )
                page = ctx.new_page()
                page.goto(
                    url, wait_until="domcontentloaded", timeout=int(timeout * 1000)
                )
                try:
                    page.wait_for_load_state("networkidle", timeout=5000)
                except Exception:
                    pass  # networkidle is best-effort
                html = page.content()
            finally:
                browser.close()
    except Exception as exc:
        raise ScrapeError(f"generic playwright fetch failed for {url}: {exc}") from exc

    return parse_jobposting_html(html, url)
