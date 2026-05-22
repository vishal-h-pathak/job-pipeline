"""Tests for jobpipe.tailor.manual.scrape_ashby.

Ashby returns the whole job board in one JSON document — we filter by
posting id parsed from the URL. Single httpx.Client mock per test.
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


def _ashby_payload():
    return json.loads((FIXTURES / "ashby_board.json").read_text())


def test_ashby_fetcher_finds_matching_posting():
    with patch("httpx.Client", return_value=_make_mock_client(_ashby_payload())):
        from jobpipe.tailor.manual.scrape_ashby import fetch_ashby_posting
        result = fetch_ashby_posting(
            "https://jobs.ashbyhq.com/eonsystems/a1b2c3d4-e5f6-7890-abcd-ef1234567890"
        )

    assert result.ats_kind == "ashby"
    assert result.confidence == "high"
    assert result.title == "Senior Software Engineer, Connectomics"
    assert result.company == "Eonsystems"
    assert result.location == "Remote, USA"
    assert "connectomics analysis platform" in result.description
    assert "Design distributed reconstruction pipelines" in result.description
    # HTML stripped:
    assert "<p>" not in result.description
    assert "<h3>" not in result.description
    assert result.url == "https://jobs.ashbyhq.com/eonsystems/a1b2c3d4-e5f6-7890-abcd-ef1234567890"


def test_ashby_fetcher_accepts_application_suffix():
    with patch("httpx.Client", return_value=_make_mock_client(_ashby_payload())):
        from jobpipe.tailor.manual.scrape_ashby import fetch_ashby_posting
        result = fetch_ashby_posting(
            "https://jobs.ashbyhq.com/eonsystems/a1b2c3d4-e5f6-7890-abcd-ef1234567890/application"
        )
    assert result.title == "Senior Software Engineer, Connectomics"


def test_ashby_fetcher_raises_scrape_error_when_posting_missing_from_board():
    """The URL parses but the posting id isn't on the board (delisted)."""
    payload = _ashby_payload()
    # remove the target posting from the board
    payload["jobs"] = [j for j in payload["jobs"]
                       if j["id"] != "a1b2c3d4-e5f6-7890-abcd-ef1234567890"]
    with patch("httpx.Client", return_value=_make_mock_client(payload)):
        from jobpipe.tailor.manual.scrape_ashby import fetch_ashby_posting
        from jobpipe.tailor.manual import ScrapeError
        with pytest.raises(ScrapeError, match="not found in board"):
            fetch_ashby_posting(
                "https://jobs.ashbyhq.com/eonsystems/a1b2c3d4-e5f6-7890-abcd-ef1234567890"
            )


def test_ashby_fetcher_rejects_non_ashby_url():
    from jobpipe.tailor.manual.scrape_ashby import fetch_ashby_posting
    from jobpipe.tailor.manual import UnsupportedUrl
    with pytest.raises(UnsupportedUrl):
        fetch_ashby_posting("https://jobs.lever.co/anthropic/abc123")


def test_ashby_fetcher_rejects_malformed_path():
    from jobpipe.tailor.manual.scrape_ashby import fetch_ashby_posting
    from jobpipe.tailor.manual import UnsupportedUrl
    with pytest.raises(UnsupportedUrl):
        fetch_ashby_posting("https://jobs.ashbyhq.com/eonsystems/")
