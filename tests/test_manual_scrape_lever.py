"""Tests for jobpipe.tailor.manual.scrape_lever.

Lever exposes a single JSON endpoint per posting, so the mock is simpler
than the Greenhouse two-call pattern.
"""

from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

FIXTURES = Path(__file__).parent / "fixtures" / "manual"


def _make_mock_client(payload: dict):
    def fake_get(url, *args, **kwargs):
        resp = MagicMock()
        resp.status_code = 200
        resp.json.return_value = payload
        resp.text = json.dumps(payload)
        resp.raise_for_status.return_value = None
        return resp

    client = MagicMock()
    client.get.side_effect = fake_get

    ctx = MagicMock()
    ctx.__enter__.return_value = client
    ctx.__exit__.return_value = False
    return ctx


def _lever_payload():
    return json.loads((FIXTURES / "lever_posting.json").read_text())


def test_lever_fetcher_parses_fixture():
    with patch("httpx.Client", return_value=_make_mock_client(_lever_payload())):
        from jobpipe.tailor.manual.scrape_lever import fetch_lever_posting
        result = fetch_lever_posting(
            "https://jobs.lever.co/anthropic/abc12345-def6-7890-abcd-ef1234567890"
        )

    assert result.ats_kind == "lever"
    assert result.confidence == "high"
    assert result.title == "Member of Technical Staff, Inference"
    assert result.company == "Anthropic"
    assert result.location == "San Francisco, CA"
    # composed description: plain + lists + additional
    assert "scale our inference infrastructure" in result.description
    assert "What you'll do:" in result.description
    assert "Optimize inference latency" in result.description
    assert "Visa sponsorship available" in result.description
    # HTML stripped:
    assert "<ul>" not in result.description
    assert result.url == "https://jobs.lever.co/anthropic/abc12345-def6-7890-abcd-ef1234567890"


def test_lever_fetcher_accepts_apply_suffix_and_strips_query():
    with patch("httpx.Client", return_value=_make_mock_client(_lever_payload())):
        from jobpipe.tailor.manual.scrape_lever import fetch_lever_posting
        result = fetch_lever_posting(
            "https://jobs.lever.co/anthropic/abc12345-def6-7890-abcd-ef1234567890/apply"
            "?lever-source=Twitter"
        )

    assert result.title == "Member of Technical Staff, Inference"
    assert "?" not in result.url
    assert "/apply" not in result.url  # canonicalized to the hostedUrl shape


def test_lever_fetcher_company_from_slug_titlecase():
    payload = _lever_payload()
    payload["hostedUrl"] = "https://jobs.lever.co/scale-ai/xyz789ab-cdef"
    with patch("httpx.Client", return_value=_make_mock_client(payload)):
        from jobpipe.tailor.manual.scrape_lever import fetch_lever_posting
        result = fetch_lever_posting(
            "https://jobs.lever.co/scale-ai/xyz789ab-cdef"
        )

    # "scale-ai" → "Scale Ai" — title-case heuristic; acronyms imperfect.
    assert result.company == "Scale Ai"


def test_lever_fetcher_rejects_non_lever_url():
    from jobpipe.tailor.manual.scrape_lever import fetch_lever_posting
    from jobpipe.tailor.manual import UnsupportedUrl

    with pytest.raises(UnsupportedUrl):
        fetch_lever_posting("https://job-boards.greenhouse.io/anthropic/jobs/123")


def test_lever_fetcher_rejects_malformed_path():
    from jobpipe.tailor.manual.scrape_lever import fetch_lever_posting
    from jobpipe.tailor.manual import UnsupportedUrl

    with pytest.raises(UnsupportedUrl):
        fetch_lever_posting("https://jobs.lever.co/anthropic/")


def test_lever_fetcher_falls_back_to_html_when_no_plaintext():
    """If descriptionPlain is missing, strip the HTML in description."""
    payload = _lever_payload()
    payload["descriptionPlain"] = ""
    payload["description"] = (
        "<p>We need a senior <strong>ML</strong> engineer.</p>"
    )
    with patch("httpx.Client", return_value=_make_mock_client(payload)):
        from jobpipe.tailor.manual.scrape_lever import fetch_lever_posting
        result = fetch_lever_posting(
            "https://jobs.lever.co/anthropic/abc12345-def6-7890-abcd-ef1234567890"
        )

    assert "<p>" not in result.description
    assert "<strong>" not in result.description
    assert "senior ML engineer" in result.description
