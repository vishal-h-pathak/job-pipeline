"""Tests for jobpipe.tailor.manual.scrape_greenhouse.

The fetcher hits two boards-api.greenhouse.io endpoints (single-job
+ board metadata) sharing one httpx.Client. We mock both via a single
patch on ``httpx.Client`` and a URL-prefix dispatcher.
"""

from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

FIXTURES = Path(__file__).parent / "fixtures" / "manual"


def _make_mock_client(responses: dict):
    """Return a context-manager mock that maps URL substring → JSON payload."""

    def fake_get(url, *args, **kwargs):
        for substr, payload in responses.items():
            if substr in url:
                resp = MagicMock()
                resp.status_code = 200
                resp.json.return_value = payload
                resp.text = json.dumps(payload)
                resp.raise_for_status.return_value = None
                return resp
        raise AssertionError(f"unexpected URL in test: {url}")

    client = MagicMock()
    client.get.side_effect = fake_get

    ctx = MagicMock()
    ctx.__enter__.return_value = client
    ctx.__exit__.return_value = False
    return ctx


def _greenhouse_responses():
    return {
        "/boards/anthropic/jobs/4123456":
            json.loads((FIXTURES / "greenhouse_job.json").read_text()),
        # Order matters: the board-metadata URL is a prefix of the job URL,
        # so we register the more-specific one first.
        "/boards/anthropic":
            json.loads((FIXTURES / "greenhouse_board.json").read_text()),
    }


def test_greenhouse_fetcher_parses_fixture():
    with patch("httpx.Client", return_value=_make_mock_client(_greenhouse_responses())):
        from jobpipe.tailor.manual.scrape_greenhouse import (
            fetch_greenhouse_posting,
        )
        result = fetch_greenhouse_posting(
            "https://job-boards.greenhouse.io/anthropic/jobs/4123456"
        )

    assert result.ats_kind == "greenhouse"
    assert result.confidence == "high"
    assert result.title == "Software Engineer, ML Research"
    assert result.company == "Anthropic"
    assert result.location == "San Francisco, CA"
    # HTML stripped to plain text:
    assert "<p>" not in result.description
    assert "training infrastructure" in result.description
    assert result.url == "https://job-boards.greenhouse.io/anthropic/jobs/4123456"


def test_greenhouse_fetcher_canonicalises_boards_alias_and_tracking_params():
    """boards.greenhouse.io is an alias for job-boards.greenhouse.io;
    tracking params like ``gh_jid`` must drop out of the canonical URL.
    """
    with patch("httpx.Client", return_value=_make_mock_client(_greenhouse_responses())):
        from jobpipe.tailor.manual.scrape_greenhouse import (
            fetch_greenhouse_posting,
        )
        result = fetch_greenhouse_posting(
            "https://boards.greenhouse.io/anthropic/jobs/4123456?gh_jid=4123456"
        )

    assert result.title == "Software Engineer, ML Research"
    assert "?" not in result.url
    assert result.url == "https://job-boards.greenhouse.io/anthropic/jobs/4123456"


def test_greenhouse_fetcher_rejects_non_greenhouse_url():
    from jobpipe.tailor.manual.scrape_greenhouse import (
        fetch_greenhouse_posting,
    )
    from jobpipe.tailor.manual import UnsupportedUrl

    with pytest.raises(UnsupportedUrl):
        fetch_greenhouse_posting("https://jobs.lever.co/anthropic/abc-123")


def test_greenhouse_fetcher_rejects_malformed_path():
    from jobpipe.tailor.manual.scrape_greenhouse import (
        fetch_greenhouse_posting,
    )
    from jobpipe.tailor.manual import UnsupportedUrl

    with pytest.raises(UnsupportedUrl):
        fetch_greenhouse_posting("https://job-boards.greenhouse.io/anthropic/")


def test_greenhouse_fetcher_raises_scrape_error_on_empty_title():
    responses = _greenhouse_responses()
    # mutate the job fixture in-memory: blank title
    bad_job = dict(responses["/boards/anthropic/jobs/4123456"])
    bad_job["title"] = ""
    responses["/boards/anthropic/jobs/4123456"] = bad_job

    from jobpipe.tailor.manual.scrape_greenhouse import (
        fetch_greenhouse_posting,
    )
    from jobpipe.tailor.manual import ScrapeError

    with patch("httpx.Client", return_value=_make_mock_client(responses)):
        with pytest.raises(ScrapeError):
            fetch_greenhouse_posting(
                "https://job-boards.greenhouse.io/anthropic/jobs/4123456"
            )
