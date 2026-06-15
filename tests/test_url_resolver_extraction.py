"""tests/test_url_resolver_extraction.py — direct-ATS extraction from
aggregator pages (`jobpipe.tailor.url_resolver._extract_ats_link_from_html`).

Fixtures live in `tests/fixtures/aggregators/`. The teal_* and talent_*
fixtures are trimmed real captures (2026-06-15); jsonld_* and anchor_* are
representative of standard structures whose live pages refused our HTTP
client (simplify.jobs TLS-blocks; jooble/learn4good 403). See the fixture
README.

Each extractor strategy is exercised against a fixture that uses it:

  JSON-LD            → jsonld_jobposting.html  → ashby URL
  embedded app JSON  → teal_*.html             → real greenhouse/workday/...
  anchor / data-attr → anchor_apply.html       → lever URL
  one-hop redirect   → inline HTML + patched _one_hop_final_url

Negatives (stay flagged): talent_onsite.html (on-site apply), malformed
HTML, and an apply link whose one-hop target is not an ATS — all return
None without raising.
"""

from __future__ import annotations

from pathlib import Path
from urllib.parse import urlparse

import pytest

from jobpipe.tailor import url_resolver
from jobpipe.tailor.url_resolver import _extract_ats_link_from_html, _is_ats

_FX = Path(__file__).resolve().parent / "fixtures" / "aggregators"


def _load(name: str) -> str:
    return (_FX / name).read_text()


# ── Strategy 2: embedded app JSON (real teal captures) ──────────────────────

@pytest.mark.parametrize(
    "fixture, expected_host_substr",
    [
        ("teal_greenhouse.html", "greenhouse.io"),
        ("teal_workday.html", "myworkdayjobs.com"),
        ("teal_icims.html", "icims.com"),
        ("teal_smartrecruiters.html", "smartrecruiters.com"),
    ],
)
def test_embedded_json_yields_direct_ats(fixture, expected_host_substr):
    html = _load(fixture)
    resolved = _extract_ats_link_from_html(html, "https://www.tealhq.com/job/x")
    assert resolved is not None, f"{fixture}: expected a direct ATS link"
    host = (urlparse(resolved).hostname or "").lower()
    assert expected_host_substr in host
    assert _is_ats(host)


# ── Strategy 1: schema.org JSON-LD JobPosting.url ───────────────────────────

def test_jsonld_jobposting_url_yields_direct_ats():
    html = _load("jsonld_jobposting.html")
    resolved = _extract_ats_link_from_html(html, "https://aggregator.example/p/1")
    assert resolved == "https://jobs.ashbyhq.com/acme/9f3c-senior-ml-engineer"


# ── Strategy 3: anchor / data-* attribute ───────────────────────────────────

def test_anchor_data_attr_yields_direct_ats():
    html = _load("anchor_apply.html")
    resolved = _extract_ats_link_from_html(html, "https://aggregator.example/p/2")
    assert resolved == "https://jobs.lever.co/acme/abc123-backend-engineer"


# ── Strategy 4: one-hop apply redirect ──────────────────────────────────────

_REDIRECT_HTML = """
<html><body>
  <a href="https://click.aggregator.example/r?job=42">Apply now</a>
</body></html>
"""


def test_one_hop_redirect_to_ats_is_accepted(monkeypatch):
    seen = {}

    def fake_hop(url):
        seen["url"] = url
        return "https://boards.greenhouse.io/acme/jobs/42"

    monkeypatch.setattr(url_resolver, "_one_hop_final_url", fake_hop)
    resolved = _extract_ats_link_from_html(
        _REDIRECT_HTML, "https://aggregator.example/p/3"
    )
    assert resolved == "https://boards.greenhouse.io/acme/jobs/42"
    assert seen["url"] == "https://click.aggregator.example/r?job=42"


def test_one_hop_redirect_to_non_ats_returns_none(monkeypatch):
    monkeypatch.setattr(
        url_resolver, "_one_hop_final_url",
        lambda url: "https://www.othersite.example/careers/42",
    )
    resolved = _extract_ats_link_from_html(
        _REDIRECT_HTML, "https://aggregator.example/p/3"
    )
    assert resolved is None


# ── Negatives: stay flagged, never raise ────────────────────────────────────

def test_onsite_apply_stays_flagged():
    html = _load("talent_onsite.html")
    resolved = _extract_ats_link_from_html(
        html, "https://www.talent.com/view?id=1", allow_one_hop=False
    )
    assert resolved is None


def test_malformed_html_returns_none_no_raise():
    assert _extract_ats_link_from_html("<html><a href=", "https://x.example") is None
    assert _extract_ats_link_from_html("", "https://x.example") is None
    assert _extract_ats_link_from_html("not html at all {", "https://x.example") is None
