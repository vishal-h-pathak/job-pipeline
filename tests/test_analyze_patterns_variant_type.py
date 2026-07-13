"""P2 (callback feedback loop) — analyze_patterns.py's resume_variant x
company_type aggregation ("which resume version wins where / which to
retire").

Synthetic ``jobs`` rows only — no Supabase round-trip. Exercises
``which_wins_where`` directly against hand-built history so the
best/worst-variant selection, the retire_candidate effect-size gate, and
the "not enough data" omission case are all pinned.

Mirrors test_response_outcomes.py's ``analyzer`` fixture: analyze_patterns
runs ``from jobpipe.db import client`` at import time, which would build
a real Supabase client (and raise in secretless CI) unless the lazy
client is stubbed first via ``patch_db_client``.
"""

from __future__ import annotations

import pytest


@pytest.fixture
def analyzer(patch_db_client):
    patch_db_client(object())
    from jobpipe.tailor.scripts import analyze_patterns as ap

    return ap


def _rows(n: int, *, company_type: str, resume_variant: str,
          applied: int, responded: int) -> list[dict]:
    """``n`` synthetic applied rows for one (company_type, resume_variant)
    cell; ``responded`` of the first ``applied`` get a real
    response_status, the rest are still pending."""
    rows = []
    for i in range(n):
        row = {
            "id": f"{company_type}-{resume_variant}-{i}",
            "company_type": company_type,
            "resume_variant": resume_variant,
            "status": "applied" if i < applied else "approved",
            "response_status": "interview" if i < responded else "none",
        }
        rows.append(row)
    return rows


def test_which_wins_where_ranks_best_and_worst_variant(analyzer):
    jobs = (
        # ai_startup: "modern" wins big over "classic"
        _rows(10, company_type="ai_startup", resume_variant="modern",
              applied=10, responded=8)
        + _rows(10, company_type="ai_startup", resume_variant="classic",
                applied=10, responded=1)
    )
    report = analyzer.which_wins_where(jobs)
    assert len(report) == 1
    entry = report[0]
    assert entry["company_type"] == "ai_startup"
    assert entry["best_variant"] == "modern"
    assert entry["worst_variant"] == "classic"
    assert entry["best_response_rate"] == 0.8
    assert entry["worst_response_rate"] == 0.1
    # 70pp gap clears the effect-size threshold -> flagged to retire.
    assert entry["retire_candidate"] == "classic"


def test_which_wins_where_reports_delta_between_variants(analyzer):
    jobs = (
        _rows(10, company_type="enterprise", resume_variant="modern",
              applied=10, responded=5)
        + _rows(10, company_type="enterprise", resume_variant="compact",
                applied=10, responded=4)
    )
    report = analyzer.which_wins_where(jobs)
    entry = report[0]
    assert entry["delta_pp"] == 10.0


def test_which_wins_where_omits_company_type_with_single_variant(analyzer):
    """Only one resume_variant seen for this company_type at n >=
    MIN_GROUP_SIZE — nothing to compare against, so it's dropped from
    the report rather than reported with a meaningless "best"."""
    jobs = _rows(analyzer.MIN_GROUP_SIZE, company_type="consultancy",
                 resume_variant="classic", applied=analyzer.MIN_GROUP_SIZE,
                 responded=2)
    report = analyzer.which_wins_where(jobs)
    assert report == []


def test_which_wins_where_omits_groups_below_min_size(analyzer):
    """Both variants present but under MIN_GROUP_SIZE -> still omitted."""
    small_n = analyzer.MIN_GROUP_SIZE - 1
    jobs = (
        _rows(small_n, company_type="other", resume_variant="modern",
              applied=small_n, responded=small_n)
        + _rows(small_n, company_type="other", resume_variant="classic",
                applied=small_n, responded=0)
    )
    report = analyzer.which_wins_where(jobs)
    assert report == []


def test_which_wins_where_empty_history_returns_empty_list(analyzer):
    assert analyzer.which_wins_where([]) == []


def test_render_markdown_includes_wins_where_section(analyzer):
    jobs = (
        _rows(10, company_type="ai_startup", resume_variant="modern",
              applied=10, responded=8)
        + _rows(10, company_type="ai_startup", resume_variant="classic",
                applied=10, responded=1)
    )
    stats = analyzer.aggregate(jobs, ("archetype",))
    report = analyzer.which_wins_where(jobs)
    md = analyzer.render_markdown(stats, [], ("archetype",), len(jobs), wins_where=report)
    assert "Which resume version wins where" in md
    assert "ai_startup" in md
    assert "modern" in md
    assert "classic" in md


def test_render_markdown_reports_insufficient_data_when_wins_where_empty(analyzer):
    md = analyzer.render_markdown({}, [], ("archetype",), 0, wins_where=[])
    assert "Not enough data" in md
