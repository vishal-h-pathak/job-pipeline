"""tests/test_url_resolver_recursion.py — multi-hop resolution in
``jobpipe.tailor.url_resolver.resolve_application_url``.

The resolver follows an extracted *source* URL (which may itself be another
aggregator or a careers landing page) until it reaches a known ATS host or the
hop cap is hit. Every hop's network I/O is isolated behind ``_fetch_page`` so
these tests run with ZERO live network — each test patches it with a canned
page map ``url -> (final_url, status_code, html, history_urls)``.
"""

from __future__ import annotations

from jobpipe.tailor import url_resolver


def _patch_pages(monkeypatch, pages):
    monkeypatch.setattr(
        url_resolver, "_fetch_page", lambda u, timeout=15.0: pages[u]
    )


def _nextdata(url: str) -> str:
    return (
        '<script id="__NEXT_DATA__" type="application/json">'
        f'{{"props":{{"job":{{"url":"{url}"}}}}}}</script>'
    )


# ── Recurse aggregator → aggregator → ATS, terminate at the ATS ─────────────


def test_recurse_aggregator_chain_terminates_at_ats(monkeypatch):
    pages = {
        "https://agg1.example/job/1": (
            "https://agg1.example/job/1", 200,
            _nextdata("https://agg2.example/posting/2"), [],
        ),
        "https://agg2.example/posting/2": (
            "https://agg2.example/posting/2", 200,
            _nextdata("https://boards.greenhouse.io/acme/jobs/99"), [],
        ),
    }
    _patch_pages(monkeypatch, pages)
    res = url_resolver.resolve_application_url("https://agg1.example/job/1")
    assert res["is_ats"] is True
    assert res["resolved"] == "https://boards.greenhouse.io/acme/jobs/99"


# ── A source URL that is itself already an ATS is accepted without re-fetch ──


def test_recurse_source_already_ats(monkeypatch):
    pages = {
        # JSON-LD url points straight at an ATS host (careers landing absent).
        "https://agg.example/job/7": (
            "https://agg.example/job/7", 200,
            '<script type="application/ld+json">'
            '{"@type":"JobPosting","url":"https://jobs.lever.co/acme/abc-eng"}'
            "</script>",
            [],
        ),
    }
    _patch_pages(monkeypatch, pages)
    res = url_resolver.resolve_application_url("https://agg.example/job/7")
    assert res["is_ats"] is True
    assert res["resolved"] == "https://jobs.lever.co/acme/abc-eng"


# ── Loop guards: same-host self-link and cross-host A→B→A both terminate ─────


def test_recurse_self_loop_terminates(monkeypatch):
    page = (
        "https://loop.example/job/1", 200,
        _nextdata("https://loop.example/job/1"), [],
    )
    _patch_pages(monkeypatch, {"https://loop.example/job/1": page})
    res = url_resolver.resolve_application_url("https://loop.example/job/1")
    assert res["is_ats"] is False
    assert res["resolved"] == "https://loop.example/job/1"


def test_recurse_cross_host_loop_terminates(monkeypatch):
    pages = {
        "https://a.example/1": (
            "https://a.example/1", 200, _nextdata("https://b.example/2"), [],
        ),
        "https://b.example/2": (
            "https://b.example/2", 200, _nextdata("https://a.example/1"), [],
        ),
    }
    _patch_pages(monkeypatch, pages)
    res = url_resolver.resolve_application_url("https://a.example/1")
    assert res["is_ats"] is False
    # Must terminate (no RecursionError) at one of the two visited hosts.
    assert res["resolved"] in ("https://a.example/1", "https://b.example/2")


# ── No embedded URL → fall back to the original, stay unverified ─────────────


def test_recurse_no_embedded_url_falls_back(monkeypatch):
    page = (
        "https://agg.example/job/9", 200,
        "<html><body>nothing actionable here</body></html>", [],
    )
    _patch_pages(monkeypatch, {"https://agg.example/job/9": page})
    res = url_resolver.resolve_application_url("https://agg.example/job/9")
    assert res["is_ats"] is False
    assert res["resolved"] == "https://agg.example/job/9"
    # The fetched page is threaded back so the hunt caller can reuse it for
    # enrich_description without a second fetch.
    assert res["html"] == "<html><body>nothing actionable here</body></html>"


# ── Direct redirect to an ATS short-circuits at hop 0 ───────────────────────


def test_direct_redirect_to_ats(monkeypatch):
    page = (
        "https://boards.greenhouse.io/acme/jobs/5", 200,
        "<html>ATS form</html>", ["https://t.co/x"],
    )
    _patch_pages(monkeypatch, {"https://t.co/x": page})
    res = url_resolver.resolve_application_url("https://t.co/x")
    assert res["is_ats"] is True
    assert res["resolved"] == "https://boards.greenhouse.io/acme/jobs/5"
    assert "https://t.co/x" in res["trail"]


# ── Hop cap stops an unbounded chain ────────────────────────────────────────


def test_recurse_respects_hop_cap(monkeypatch):
    calls = []

    def fake(u, timeout=15.0):
        calls.append(u)
        n = int(u.rsplit("/", 1)[1])
        return (u, 200, _nextdata(f"https://h{n + 1}.example/{n + 1}"), [])

    monkeypatch.setattr(url_resolver, "_fetch_page", fake)
    res = url_resolver.resolve_application_url("https://h0.example/0")
    assert res["is_ats"] is False
    assert len(calls) == 3  # default max_hops
