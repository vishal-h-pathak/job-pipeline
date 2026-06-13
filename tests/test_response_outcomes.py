"""tests/test_response_outcomes.py — Session I: outcomes schema (migration 012).

jobs.response_status ('none'|'rejected'|'screen'|'interview'|'offer')
+ responded_at carry what the EMPLOYER did after 'applied' — a separate
axis from the canonical pipeline status. Pins:

- the analyzer's buckets consume the real column (pre-012 rows and
  'none' count nowhere),
- the rate properties compute from those real counters,
- the SQL migration's CHECK enum and the analyzer's sets agree.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

_MIGRATION = (
    Path(__file__).resolve().parent.parent
    / "jobpipe" / "tailor" / "scripts" / "012_response_outcomes.sql"
)


@pytest.fixture
def analyzer(patch_db_client):
    """Import analyze_patterns with the lazy db client stubbed out.

    The module runs ``from jobpipe.db import client`` at import time,
    which would otherwise build a real Supabase client (and raise in
    secretless CI).
    """
    patch_db_client(object())
    from jobpipe.tailor.scripts import analyze_patterns as ap

    return ap


def test_aggregate_consumes_response_status(analyzer):
    jobs = [
        {"archetype": "a", "status": "applied", "response_status": "interview"},
        {"archetype": "a", "status": "applied", "response_status": "rejected"},
        {"archetype": "a", "status": "applied", "response_status": "screen"},
        {"archetype": "a", "status": "applied", "response_status": "offer"},
        {"archetype": "a", "status": "applied", "response_status": "none"},
        {"archetype": "a", "status": "applied"},  # pre-012 row, no column
        {"archetype": "a", "status": "new", "response_status": "none"},
    ]
    stats = analyzer.aggregate(jobs, ("archetype",))
    s = stats["a"]
    assert s.n == 7
    assert s.applied == 6
    assert s.responded == 4      # rejected + screen + interview + offer
    assert s.interviewed == 2    # interview + offer
    assert s.offered == 1

    assert s.applied_rate == pytest.approx(6 / 7)
    assert s.response_rate == pytest.approx(4 / 6)
    assert s.interview_rate == pytest.approx(2 / 6)
    assert s.offer_rate == pytest.approx(1 / 6)


def test_status_no_longer_feeds_outcome_buckets(analyzer):
    """The retired pre-012 behavior read outcomes from jobs.status."""
    jobs = [{"archetype": "a", "status": "interview"}]  # not a canonical status
    s = analyzer.aggregate(jobs, ("archetype",))["a"]
    assert s.responded == 0 and s.interviewed == 0 and s.offered == 0


def test_migration_and_analyzer_agree_on_the_enum(analyzer):
    sql = _MIGRATION.read_text(encoding="utf-8")
    m = re.search(r"CHECK \(response_status IN \(([^)]*)\)\)", sql)
    assert m, "migration must carry the response_status CHECK enum"
    sql_values = {v.strip().strip("'") for v in m.group(1).split(",")}
    assert sql_values == analyzer.RESPONSE_STATUSES | {"none"}
    assert analyzer.INTERVIEW_RESPONSES < analyzer.RESPONSE_STATUSES
    assert analyzer.OFFER_RESPONSES < analyzer.INTERVIEW_RESPONSES
    assert "responded_at TIMESTAMPTZ" in sql
    assert "DEFAULT 'none'" in sql
