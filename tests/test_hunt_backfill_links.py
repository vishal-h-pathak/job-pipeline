"""tests/test_hunt_backfill_links.py — one-off aggregator-link backfill.

Pins the behavior of jobpipe/hunt/scripts/backfill_links.py:

  - candidate selection: status in {new, approved}, url is an aggregator
    (not already a direct ATS), application_url IS NULL
  - reuses the hunt gate's _resolve_link_and_liveness (resolve + classify)
  - positive dead signal → expire (status + link_status = 'expired')
  - approved rows are NEVER suspicious-dropped — they stay approved unless
    positively dead (the backfill spends zero LLM, so the post-score
    suspicious gate is intentionally not applied to anything)
  - dry-run by default writes nothing; --commit writes status-preserving
    link updates

HTTP is mocked at the resolver seam (agent.resolve_application_url); the
real _resolve_link_and_liveness + classify_posting run.
"""

from __future__ import annotations

import pytest

from jobpipe.hunt import agent
from jobpipe.hunt.scripts import backfill_links as bf


GH = "https://boards.greenhouse.io/acme/jobs/1"
AGG_OK = "https://talent.com/view?id=ok"
AGG_DEAD = "https://talent.com/view?id=dead"
AGG_UNRES = "https://learn4good.com/job/unres"


@pytest.fixture(autouse=True)
def _no_sleep(monkeypatch):
    monkeypatch.setattr(bf, "_polite_sleep", lambda: None)


def _resolve_stub(**by_url):
    def _fake(url, *a, **k):
        return by_url[url]
    return _fake


# ── Fake Supabase client ─────────────────────────────────────────────────

class _Exec:
    def __init__(self, data):
        self.data = data


class _Select:
    def __init__(self, rows):
        self._rows = rows

    def select(self, *a, **k):
        return self

    def in_(self, *a, **k):
        return self

    def execute(self):
        return _Exec(self._rows)


class _Update:
    def __init__(self, sink, payload):
        self._sink = sink
        self._payload = payload

    def eq(self, col, val):
        self._sink.append({"id": val, "payload": self._payload})
        return self

    def execute(self):
        return _Exec([])


class _Table:
    def __init__(self, rows, sink):
        self._rows = rows
        self._sink = sink

    def select(self, *a, **k):
        return _Select(self._rows)

    def update(self, payload):
        return _Update(self._sink, payload)


class _FakeClient:
    def __init__(self, rows):
        self.rows = rows
        self.writes: list[dict] = []

    def table(self, name):
        return _Table(self.rows, self.writes)


# ── Candidate selection ──────────────────────────────────────────────────

def test_is_candidate_filters():
    # aggregator, unresolved → candidate
    assert bf._is_candidate({"url": AGG_OK, "application_url": None})
    # already resolved → not a candidate (idempotent re-runs skip it)
    assert not bf._is_candidate({"url": AGG_OK, "application_url": GH})
    # already a direct ATS url → not a candidate
    assert not bf._is_candidate({"url": GH, "application_url": None})
    # no url → not a candidate
    assert not bf._is_candidate({"url": "", "application_url": None})


def test_fetch_candidates_filters_resolved_and_direct():
    client = _FakeClient([
        {"id": "a", "url": AGG_OK, "application_url": None, "status": "new"},
        {"id": "b", "url": GH, "application_url": None, "status": "approved"},
        {"id": "c", "url": AGG_OK, "application_url": GH, "status": "new"},
    ])
    cands = bf.fetch_candidates(client)
    assert [r["id"] for r in cands] == ["a"]


# ── plan_change ──────────────────────────────────────────────────────────

def test_plan_resolve(monkeypatch):
    monkeypatch.setattr(agent, "resolve_application_url", _resolve_stub(**{
        AGG_OK: {"resolved": GH, "is_ats": True, "status_code": 200,
                 "html": "<p>apply</p>"},
    }))
    change = bf.plan_change({"id": "a", "company": "Acme", "url": AGG_OK,
                             "status": "approved"})
    assert change.action == "resolve"
    assert change.application_url == GH
    assert change.ats_kind == "greenhouse"
    assert change.link_status == "direct"


def test_plan_expire_on_dead(monkeypatch):
    monkeypatch.setattr(agent, "resolve_application_url", _resolve_stub(**{
        AGG_DEAD: {"resolved": AGG_DEAD, "is_ats": False, "status_code": 404,
                   "html": ""},
    }))
    change = bf.plan_change({"id": "d", "company": "X", "url": AGG_DEAD,
                             "status": "approved"})
    assert change.action == "expire"
    assert change.link_status == "expired"
    assert "404" in change.reason


def test_approved_unresolvable_stays_approved_not_dropped(monkeypatch):
    """Manually-approved aggregator that can't be resolved must NOT be
    expired or skipped — only flagged. (No suspicious gate in backfill.)"""
    monkeypatch.setattr(agent, "resolve_application_url", _resolve_stub(**{
        AGG_UNRES: {"resolved": AGG_UNRES, "is_ats": False, "status_code": 200,
                    "html": "<p>vague reposting</p>"},
    }))
    change = bf.plan_change({"id": "u", "company": "Y", "url": AGG_UNRES,
                             "status": "approved"})
    assert change.action == "resolve"
    assert change.link_status == "aggregator_unverified"


# ── run(): dry-run vs commit ─────────────────────────────────────────────

def _client_with_one(url, status="approved"):
    return _FakeClient([
        {"id": "r1", "company": "Acme", "url": url,
         "application_url": None, "status": status},
    ])


def test_dry_run_writes_nothing(monkeypatch):
    monkeypatch.setattr(agent, "resolve_application_url", _resolve_stub(**{
        AGG_OK: {"resolved": GH, "is_ats": True, "status_code": 200, "html": "x"},
    }))
    client = _client_with_one(AGG_OK)
    counts = bf.run(commit=False, client=client)
    assert counts["resolved"] == 1
    assert counts["written"] == 0
    assert client.writes == []


def test_commit_writes_status_preserving_link_update(monkeypatch):
    monkeypatch.setattr(agent, "resolve_application_url", _resolve_stub(**{
        AGG_OK: {"resolved": GH, "is_ats": True, "status_code": 200, "html": "x"},
    }))
    client = _client_with_one(AGG_OK, status="approved")
    counts = bf.run(commit=True, client=client)
    assert counts["written"] == 1
    assert len(client.writes) == 1
    payload = client.writes[0]["payload"]
    assert payload["application_url"] == GH
    assert payload["ats_kind"] == "greenhouse"
    assert payload["link_status"] == "direct"
    # Status must NOT be touched on a resolve (approved stays approved).
    assert "status" not in payload


def test_run_prints_per_row_and_accepts_statuses(monkeypatch, capsys):
    """Per-row output (id, url → application_url) prints as each row
    resolves, and run() honors a narrowed status slice."""
    monkeypatch.setattr(agent, "resolve_application_url", _resolve_stub(**{
        AGG_OK: {"resolved": GH, "is_ats": True, "status_code": 200, "html": "x"},
    }))
    client = _FakeClient([
        {"id": "r1", "company": "Acme", "url": AGG_OK,
         "application_url": None, "status": "approved"},
    ])
    counts = bf.run(commit=True, client=client, statuses=("approved",))
    assert counts["written"] == 1

    out = capsys.readouterr().out
    assert "[1/1]" in out          # per-row progress marker
    assert "r1" in out
    assert AGG_OK in out and GH in out   # old url → resolved application_url
    assert "[approved]" in out      # the status slice header


def test_commit_expires_dead_row(monkeypatch):
    monkeypatch.setattr(agent, "resolve_application_url", _resolve_stub(**{
        AGG_DEAD: {"resolved": AGG_DEAD, "is_ats": False, "status_code": 410,
                   "html": ""},
    }))
    client = _client_with_one(AGG_DEAD, status="approved")
    counts = bf.run(commit=True, client=client)
    assert counts["expired"] == 1
    payload = client.writes[0]["payload"]
    assert payload["status"] == "expired"
    assert payload["link_status"] == "expired"
