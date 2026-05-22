"""Tests for jobpipe.tailor.manual.scrape_generic.parse_jobposting_html.

We unit-test the pure HTML parser. The Playwright wrapper
(fetch_generic_posting) is covered by step ⑥ live verification.
"""

from __future__ import annotations

from pathlib import Path

import pytest

FIXTURES = Path(__file__).parent / "fixtures" / "manual"


def _read(name: str) -> str:
    return (FIXTURES / name).read_text()


def test_generic_parser_prefers_jsonld_jobposting():
    from jobpipe.tailor.manual.scrape_generic import parse_jobposting_html

    html = _read("generic_jobposting.html")
    result = parse_jobposting_html(html, "https://example.com/jobs/ml-research-engineer")

    assert result.ats_kind == "generic"
    assert result.confidence == "low"  # generic is ALWAYS low-confidence
    assert result.title == "ML Research Engineer"
    assert result.company == "Curated"
    assert result.location == "Brooklyn, NY, US"
    assert "retrieval-augmented systems research" in result.description
    assert "Sparse retrieval models" in result.description
    # JSON-LD description had embedded HTML — stripped:
    assert "<p>" not in result.description
    assert "<ul>" not in result.description
    assert result.url == "https://example.com/jobs/ml-research-engineer"


def test_generic_parser_falls_back_to_og_when_no_jsonld():
    from jobpipe.tailor.manual.scrape_generic import parse_jobposting_html

    html = _read("generic_no_jsonld.html")
    result = parse_jobposting_html(html, "https://smallco.example.com/careers/embedded")

    assert result.confidence == "low"
    assert result.title == "Senior Embedded Engineer"
    assert result.company == "Smallco"
    assert result.description == "Own the firmware stack for our next-generation sensor head."
    assert result.location is None  # no location on the og: path


def test_generic_parser_strips_company_suffix_from_title():
    """When og:title is missing but <title> includes ' — Company', drop the tail."""
    from jobpipe.tailor.manual.scrape_generic import parse_jobposting_html

    html = """<html><head>
        <title>Staff Software Engineer - Acme</title>
        <meta property="og:site_name" content="Acme">
    </head><body><h1>Staff Software Engineer</h1></body></html>"""
    result = parse_jobposting_html(html, "https://acme.example.com/jobs/staff-swe")
    assert result.title == "Staff Software Engineer"
    assert result.company == "Acme"


def test_generic_parser_raises_when_no_title_recoverable():
    from jobpipe.tailor.manual.scrape_generic import parse_jobposting_html
    from jobpipe.tailor.manual import ScrapeError

    html = "<html><body><p>Hello</p></body></html>"
    with pytest.raises(ScrapeError, match="no title found"):
        parse_jobposting_html(html, "https://void.example.com/")


def test_generic_parser_handles_jsonld_in_graph_wrapper():
    """JSON-LD often comes wrapped in an @graph array — must still find JobPosting."""
    from jobpipe.tailor.manual.scrape_generic import parse_jobposting_html

    html = """<html><head><script type="application/ld+json">
    {
      "@context": "https://schema.org",
      "@graph": [
        {"@type": "Organization", "name": "Orgwrap"},
        {"@type": "JobPosting", "title": "Wrapped Role",
         "description": "From inside @graph.",
         "hiringOrganization": {"@type": "Organization", "name": "Orgwrap"}}
      ]
    }
    </script></head><body><h1>x</h1></body></html>"""
    result = parse_jobposting_html(html, "https://orgwrap.example.com/jobs/1")
    assert result.title == "Wrapped Role"
    assert result.company == "Orgwrap"
