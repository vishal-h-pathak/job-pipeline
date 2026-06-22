"""tests/test_url_resolver_headless.py — the gated Playwright fallback.

The static path can't crack JS-driven Apply flows (TealHQ's button fires JS,
no embedded source URL). The headless fallback drives a real browser, but it is
EXPENSIVE + network-bound, so it is:

  * gated behind ``JOBPIPE_RESOLVE_HEADLESS`` (default OFF) for the cron hunt,
  * isolated behind ``_headless_driver`` (the only Playwright seam) so the
    public ``resolve_to_ats_headless`` policy is testable with zero browser.
"""

from __future__ import annotations

from jobpipe.tailor import url_resolver


def test_resolve_headless_enabled_reads_env(monkeypatch):
    monkeypatch.delenv("JOBPIPE_RESOLVE_HEADLESS", raising=False)
    assert url_resolver.resolve_headless_enabled() is False
    for truthy in ("1", "true", "TRUE", "yes", "on"):
        monkeypatch.setenv("JOBPIPE_RESOLVE_HEADLESS", truthy)
        assert url_resolver.resolve_headless_enabled() is True
    for falsy in ("0", "false", "no", ""):
        monkeypatch.setenv("JOBPIPE_RESOLVE_HEADLESS", falsy)
        assert url_resolver.resolve_headless_enabled() is False


def test_headless_resolver_returns_ats_url(monkeypatch):
    monkeypatch.setattr(
        url_resolver, "_headless_driver",
        lambda u, **k: "https://boards.greenhouse.io/acme/jobs/1",
    )
    out = url_resolver.resolve_to_ats_headless("https://www.tealhq.com/job/x")
    assert out == "https://boards.greenhouse.io/acme/jobs/1"


def test_headless_resolver_non_ats_returns_none(monkeypatch):
    """Landed on a careers page but never reached a true ATS → no upgrade."""
    monkeypatch.setattr(
        url_resolver, "_headless_driver",
        lambda u, **k: "https://careers.qualcomm.com/landing",
    )
    assert url_resolver.resolve_to_ats_headless("https://www.tealhq.com/job/x") is None


def test_headless_resolver_driver_error_returns_none(monkeypatch):
    def boom(u, **k):
        raise RuntimeError("no browser available")

    monkeypatch.setattr(url_resolver, "_headless_driver", boom)
    assert url_resolver.resolve_to_ats_headless("https://x.example") is None
