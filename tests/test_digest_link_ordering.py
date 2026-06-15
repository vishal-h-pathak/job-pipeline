"""tests/test_digest_link_ordering.py — Part C: within a digest tier,
direct-ATS rows sort above aggregator_unverified rows (and keep the ⚠ flag),
but tier order is untouched so a high-score Tier 1 is never buried.
"""

from __future__ import annotations

from jobpipe.notify import _render_digest


def _entry(title, tier, score, link_status):
    return {
        "job": {
            "title": title,
            "company": "Co",
            "location": "Remote",
            "url": "https://x.example",
            "link_status": link_status,
        },
        "score": {"tier": tier, "score": score, "reasoning": "r"},
    }


def _order(body, *titles):
    return [body.index(t) for t in titles]


def test_direct_sorts_above_unverified_within_tier():
    # Same tier: the unverified row scores HIGHER but must render below the
    # direct row (light down-weight) — and must still be present + flagged.
    entries = [
        _entry("UnverifiedHigh", 1, 9, "aggregator_unverified"),
        _entry("DirectLow", 1, 7, "direct"),
    ]
    _subject, body = _render_digest(entries)
    di, ui = _order(body, "DirectLow", "UnverifiedHigh")
    assert di < ui, "direct row should render above the unverified row"
    assert "UnverifiedHigh" in body  # not dropped
    assert "⚠ unverified link" in body  # flag kept


def test_tier_order_not_buried_by_link_status():
    # A high-score Tier 1 (even unverified) still leads a lower-tier direct row.
    entries = [
        _entry("Tier2Direct", 2, 10, "direct"),
        _entry("Tier1Unverified", 1, 8, "aggregator_unverified"),
    ]
    _subject, body = _render_digest(entries)
    t1, t2 = _order(body, "Tier1Unverified", "Tier2Direct")
    assert t1 < t2, "Tier 1 must lead Tier 2 regardless of link_status"


def test_within_tier_ties_break_by_score():
    # Two direct rows in the same tier still order by score desc.
    entries = [
        _entry("DirectLow", 1, 6, "direct"),
        _entry("DirectHigh", 1, 9, "direct"),
    ]
    _subject, body = _render_digest(entries)
    hi, lo = _order(body, "DirectHigh", "DirectLow")
    assert hi < lo
