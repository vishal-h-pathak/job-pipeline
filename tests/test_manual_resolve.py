"""Tests for jobpipe.tailor.manual.resolve.resolve_url.

We mock the four per-ATS fetchers + the aggregator-redirect helper.
Each test verifies the right fetcher was selected for the resolved
URL and that aggregator redirects are followed before ATS detection.
"""

from __future__ import annotations

from unittest.mock import patch

from jobpipe.tailor.manual import ScrapedPosting


def _posting(ats: str, confidence: str = "high") -> ScrapedPosting:
    return ScrapedPosting(
        url=f"https://example.com/{ats}",
        title="Some Role",
        company="Co",
        location=None,
        description="",
        ats_kind=ats,
        confidence=confidence,  # type: ignore[arg-type]
    )


def _no_redirect(url):
    return {"resolved": url, "is_ats": False, "trail": [url], "notes": ""}


def test_resolve_url_dispatches_to_greenhouse():
    with patch(
        "jobpipe.tailor.manual.resolve.resolve_application_url",
        side_effect=_no_redirect,
    ), patch(
        "jobpipe.tailor.manual.resolve.fetch_greenhouse_posting",
        return_value=_posting("greenhouse"),
    ) as gh, patch(
        "jobpipe.tailor.manual.resolve.fetch_lever_posting"
    ) as lv, patch(
        "jobpipe.tailor.manual.resolve.fetch_ashby_posting"
    ) as ah, patch(
        "jobpipe.tailor.manual.resolve.fetch_generic_posting"
    ) as gen:
        from jobpipe.tailor.manual.resolve import resolve_url
        result = resolve_url("https://job-boards.greenhouse.io/co/jobs/1")

    assert result.ats_kind == "greenhouse"
    gh.assert_called_once_with("https://job-boards.greenhouse.io/co/jobs/1")
    lv.assert_not_called()
    ah.assert_not_called()
    gen.assert_not_called()


def test_resolve_url_dispatches_to_lever():
    with patch(
        "jobpipe.tailor.manual.resolve.resolve_application_url",
        side_effect=_no_redirect,
    ), patch(
        "jobpipe.tailor.manual.resolve.fetch_lever_posting",
        return_value=_posting("lever"),
    ) as lv:
        from jobpipe.tailor.manual.resolve import resolve_url
        result = resolve_url("https://jobs.lever.co/co/abc-123")
    assert result.ats_kind == "lever"
    lv.assert_called_once()


def test_resolve_url_dispatches_to_ashby():
    with patch(
        "jobpipe.tailor.manual.resolve.resolve_application_url",
        side_effect=_no_redirect,
    ), patch(
        "jobpipe.tailor.manual.resolve.fetch_ashby_posting",
        return_value=_posting("ashby"),
    ) as ah:
        from jobpipe.tailor.manual.resolve import resolve_url
        result = resolve_url("https://jobs.ashbyhq.com/co/abc-123")
    assert result.ats_kind == "ashby"
    ah.assert_called_once()


def test_resolve_url_falls_through_to_generic_for_unknown_ats():
    with patch(
        "jobpipe.tailor.manual.resolve.resolve_application_url",
        side_effect=_no_redirect,
    ), patch(
        "jobpipe.tailor.manual.resolve.fetch_generic_posting",
        return_value=_posting("generic", confidence="low"),
    ) as gen:
        from jobpipe.tailor.manual.resolve import resolve_url
        result = resolve_url("https://acme.example.com/careers/role")

    assert result.ats_kind == "generic"
    assert result.confidence == "low"
    gen.assert_called_once_with("https://acme.example.com/careers/role")


def test_resolve_url_follows_aggregator_redirect_before_dispatch():
    """If resolve_application_url surfaces a different URL, we must use
    that for ATS detection and for the fetcher call."""
    aggregator = "https://remotive.com/remote-jobs/12345-ml-engineer"
    real = "https://job-boards.greenhouse.io/anthropic/jobs/4123456"

    def fake_resolve(u):
        assert u == aggregator
        return {"resolved": real, "is_ats": True, "trail": [u, real], "notes": "extracted from aggregator (remotive.com)"}

    with patch(
        "jobpipe.tailor.manual.resolve.resolve_application_url",
        side_effect=fake_resolve,
    ), patch(
        "jobpipe.tailor.manual.resolve.fetch_greenhouse_posting",
        return_value=_posting("greenhouse"),
    ) as gh:
        from jobpipe.tailor.manual.resolve import resolve_url
        resolve_url(aggregator)

    gh.assert_called_once_with(real)
