"""tests/test_hunt_direct_listings.py — hunt-time direct-listing gate.

Pins the "middle path" discovery policy added to
``jobpipe.hunt.agent._execute``:

  - direct ATS URLs surface clean with NO extra fetch
  - aggregator links resolve to the real ATS (link_status='direct')
  - a positive dead/closed signal drops the posting BEFORE scoring
  - an unresolvable aggregator that the scorer rates 'suspicious' is
    dropped post-score (recorded 'skipped', not notified)
  - an unresolvable-but-not-suspicious aggregator surfaces flagged
  - a transient fetch failure NEVER drops a job (false-positive safety)

HTTP is fully mocked — no network. ``resolve_application_url`` is the only
seam stubbed; ``classify_posting`` / ``is_ats_url`` / ``detect_ats`` run for
real so the wiring is exercised end to end.
"""

from __future__ import annotations

import pytest

from jobpipe.hunt import agent


GREENHOUSE = "https://boards.greenhouse.io/acme/jobs/123"
AGG = "https://talent.com/view?id=xyz"


def _job(url=AGG, **extra):
    return {
        "id": extra.get("id", "j1"),
        "title": "Research Engineer",
        "company": "Acme",
        "location": "Remote",
        "description": "x" * 400,  # long enough that enrich is a no-op
        "url": url,
        "source": "test",
        **extra,
    }


def _resolve_stub(**by_url):
    """Build a resolve_application_url replacement keyed by input URL."""
    def _fake(url, *a, **k):
        return by_url[url]
    return _fake


# ── _resolve_link_and_liveness ──────────────────────────────────────────────

def test_direct_ats_url_no_fetch(monkeypatch):
    def _boom(*a, **k):
        raise AssertionError("must not fetch/resolve a direct ATS URL")

    monkeypatch.setattr(agent, "resolve_application_url", _boom)
    job = _job(url=GREENHOUSE)

    decision, html = agent._resolve_link_and_liveness(job)

    assert decision == "ok"
    assert html is None
    assert job["link_status"] == "direct"
    assert job["application_url"] == GREENHOUSE
    assert job["ats_kind"] == "greenhouse"


def test_aggregator_resolves_to_ats_is_direct(monkeypatch):
    monkeypatch.setattr(agent, "resolve_application_url", _resolve_stub(**{
        AGG: {
            "resolved": GREENHOUSE, "is_ats": True,
            "status_code": 200, "html": "<p>Apply now</p>",
        },
    }))
    job = _job()

    decision, html = agent._resolve_link_and_liveness(job)

    assert decision == "ok"
    assert job["link_status"] == "direct"
    assert job["application_url"] == GREENHOUSE
    assert job["ats_kind"] == "greenhouse"


def test_aggregator_dead_phrase_is_dropped(monkeypatch):
    monkeypatch.setattr(agent, "resolve_application_url", _resolve_stub(**{
        AGG: {
            "resolved": AGG, "is_ats": False, "status_code": 200,
            "html": "<p>This position is no longer available.</p>",
        },
    }))
    job = _job()

    decision, _ = agent._resolve_link_and_liveness(job)

    assert decision == "dead"
    assert job["link_status"] == "expired"


def test_aggregator_unresolvable_is_flagged(monkeypatch):
    monkeypatch.setattr(agent, "resolve_application_url", _resolve_stub(**{
        AGG: {
            "resolved": AGG, "is_ats": False, "status_code": 200,
            "html": "<p>Great role, apply today!</p>",
        },
    }))
    job = _job()

    decision, _ = agent._resolve_link_and_liveness(job)

    assert decision == "ok"
    assert job["link_status"] == "aggregator_unverified"
    assert job["application_url"] == AGG


def test_transient_fetch_never_drops(monkeypatch):
    # Resolver swallowed a Timeout → status_code=None, html=None.
    monkeypatch.setattr(agent, "resolve_application_url", _resolve_stub(**{
        AGG: {
            "resolved": AGG, "is_ats": False, "status_code": None,
            "html": None, "notes": "error: timeout",
        },
    }))
    job = _job()

    decision, html = agent._resolve_link_and_liveness(job)

    assert decision == "ok", "a transient failure must never drop a job"
    assert job["link_status"] == "aggregator_unverified"


# ── Gated headless fallback upgrade ─────────────────────────────────────────

def test_aggregator_headless_upgrade_when_enabled(monkeypatch):
    """With JOBPIPE_RESOLVE_HEADLESS on, an unverified aggregator that the
    static path couldn't crack is upgraded to 'direct' when the headless
    resolver reaches a real ATS."""
    monkeypatch.setattr(agent, "resolve_application_url", _resolve_stub(**{
        AGG: {"resolved": AGG, "is_ats": False, "status_code": 200,
              "html": "<p>apply</p>"},
    }))
    monkeypatch.setattr(agent, "resolve_headless_enabled", lambda: True)
    monkeypatch.setattr(agent, "resolve_to_ats_headless", lambda url, **k: GREENHOUSE)
    job = _job()

    decision, _ = agent._resolve_link_and_liveness(job)

    assert decision == "ok"
    assert job["link_status"] == "direct"
    assert job["application_url"] == GREENHOUSE
    assert job["ats_kind"] == "greenhouse"


def test_aggregator_headless_not_called_when_disabled(monkeypatch):
    monkeypatch.setattr(agent, "resolve_application_url", _resolve_stub(**{
        AGG: {"resolved": AGG, "is_ats": False, "status_code": 200,
              "html": "<p>apply</p>"},
    }))
    monkeypatch.setattr(agent, "resolve_headless_enabled", lambda: False)

    def _boom(*a, **k):
        raise AssertionError("headless must not run when the flag is off")

    monkeypatch.setattr(agent, "resolve_to_ats_headless", _boom)
    job = _job()

    decision, _ = agent._resolve_link_and_liveness(job)

    assert decision == "ok"
    assert job["link_status"] == "aggregator_unverified"
    assert job["application_url"] == AGG


def test_aggregator_headless_miss_stays_flagged(monkeypatch):
    """Headless enabled but it couldn't reach an ATS → posting stays honestly
    flagged as aggregator_unverified (not silently passed as a clean form)."""
    monkeypatch.setattr(agent, "resolve_application_url", _resolve_stub(**{
        AGG: {"resolved": AGG, "is_ats": False, "status_code": 200,
              "html": "<p>apply</p>"},
    }))
    monkeypatch.setattr(agent, "resolve_headless_enabled", lambda: True)
    monkeypatch.setattr(agent, "resolve_to_ats_headless", lambda url, **k: None)
    job = _job()

    decision, _ = agent._resolve_link_and_liveness(job)

    assert decision == "ok"
    assert job["link_status"] == "aggregator_unverified"
    assert job["application_url"] == AGG


# ── _drop_as_suspicious (post-score gate) ───────────────────────────────────

@pytest.mark.parametrize(
    "link_status,legitimacy,expected",
    [
        ("aggregator_unverified", "suspicious", True),
        ("aggregator_unverified", "proceed_with_caution", False),
        ("aggregator_unverified", "high_confidence", False),
        ("direct", "suspicious", False),
    ],
)
def test_drop_as_suspicious(link_status, legitimacy, expected):
    job = {"link_status": link_status}
    result = {"legitimacy": legitimacy}
    assert agent._drop_as_suspicious(job, result) is expected


# ── _execute integration: ordering + gates ─────────────────────────────────

def test_execute_drops_dead_before_score_and_skips_suspicious(monkeypatch):
    jobs = [
        _job(url=GREENHOUSE, id="direct1"),
        _job(url="https://talent.com/dead", id="dead1"),
        _job(url="https://talent.com/sus", id="sus1"),
    ]

    monkeypatch.setattr(agent, "get_seen_ids", lambda: set())
    monkeypatch.setattr(agent, "iter_all_jobs", lambda: iter(jobs))
    monkeypatch.setattr(agent, "validate_url", lambda url: True)
    monkeypatch.setattr(agent, "enrich_description",
                        lambda job, prefetched_html=None: job)

    monkeypatch.setattr(agent, "resolve_application_url", _resolve_stub(**{
        "https://talent.com/dead": {
            "resolved": "https://talent.com/dead", "is_ats": False,
            "status_code": 200,
            "html": "<p>no longer accepting applications</p>",
        },
        "https://talent.com/sus": {
            "resolved": "https://talent.com/sus", "is_ats": False,
            "status_code": 200, "html": "<p>vague reposting</p>",
        },
    }))

    scored: list[str] = []

    def _score(*, title, company, description, location):
        scored.append(title)
        # The only job that reaches the scorer here is the suspicious one
        # (direct ATS is scored too); return suspicious for the agg, clean
        # otherwise.
        return {"score": 8, "tier": 1, "legitimacy": "suspicious",
                "recommended_action": "notify"}

    monkeypatch.setattr(agent, "score_job", _score)
    monkeypatch.setattr(agent, "should_notify", lambda result: True)

    upserts: list[dict] = []

    def _upsert(job, result=None, *, status=None):
        upserts.append({"id": job["id"], "status": status,
                        "link_status": job.get("link_status")})

    monkeypatch.setattr(agent, "upsert_job", _upsert)

    notified: list[list] = []
    monkeypatch.setattr(agent, "send_digest", lambda entries: notified.append(entries))

    agent._execute()

    # Dead posting dropped BEFORE scoring.
    assert "Research Engineer" in scored  # direct + suspicious reach scorer
    by_id = {u["id"]: u for u in upserts}
    assert by_id["dead1"]["status"] == "expired"
    assert by_id["dead1"]["link_status"] == "expired"
    # Suspicious unverified aggregator recorded skipped, not notified.
    assert by_id["sus1"]["status"] == "skipped"
    # Direct ATS surfaced clean and notified.
    assert by_id["direct1"]["link_status"] == "direct"
    notified_ids = {e["job"]["id"] for e in (notified[0] if notified else [])}
    assert "direct1" in notified_ids
    assert "sus1" not in notified_ids
    assert "dead1" not in notified_ids


# ── Digest rendering: prefer application_url + flag unverified ──────────────

def test_digest_prefers_application_url_and_flags_unverified():
    from jobpipe import notify

    direct = {
        "title": "Eng", "company": "Acme", "location": "Remote",
        "url": "https://talent.com/raw",
        "application_url": "https://boards.greenhouse.io/acme/jobs/9",
        "link_status": "direct",
    }
    unverified = {
        "title": "Eng2", "company": "Beta", "location": "Remote",
        "url": "https://learn4good.com/job/abc",
        "link_status": "aggregator_unverified",
    }
    score = {"score": 8, "tier": 1, "reasoning": "good fit"}

    direct_card = notify._render_job(direct, score)
    assert "boards.greenhouse.io/acme/jobs/9" in direct_card
    assert "talent.com/raw" not in direct_card
    assert "unverified link" not in direct_card

    unverified_card = notify._render_job(unverified, score)
    # No application_url → falls back to the raw url, and carries the flag.
    assert "learn4good.com/job/abc" in unverified_card
    assert "unverified link" in unverified_card
