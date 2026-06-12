"""Session G — jobpipe-hunt --rescore: dry-run math, status guards, writes.

No live Supabase or Anthropic calls: the db service client is patched
via the local ``patch_db_service_client`` fixture and score_job via
monkeypatch.
"""

from __future__ import annotations

import math

import pytest

from jobpipe.hunt import rescore

THESIS_FIXTURE = "# Hunting Thesis\n\nTier 1.5 / degree-gate fixture.\n"


# ── Chainable Supabase double ────────────────────────────────────────────


class _FakeQuery:
    def __init__(self, rows: list[dict]):
        self._rows = rows
        self.update_payload: dict | None = None
        self.eq_calls: list[tuple[str, object]] = []
        self.gte_calls: list[tuple[str, object]] = []
        self._mode = None

    def select(self, _cols):
        self._mode = "select"
        return self

    def update(self, payload):
        self._mode = "update"
        self.update_payload = payload
        return self

    def eq(self, col, val):
        self.eq_calls.append((col, val))
        return self

    def gte(self, col, val):
        self.gte_calls.append((col, val))
        return self

    def order(self, *_a, **_kw):
        return self

    def execute(self):
        rows = self._rows if self._mode == "select" else self._update_result()

        class _R:
            data = rows

        return _R()

    def _update_result(self):
        # Simulate the optimistic status guard: the update matched iff
        # every eq() filter matches the (single) backing row.
        if not self._rows:
            return []
        row = self._rows[0]
        for col, val in self.eq_calls:
            if row.get(col) != val:
                return []
        return [row]


class _FakeClient:
    def __init__(self, rows: list[dict] | None = None):
        self.query = _FakeQuery(rows or [])

    def table(self, _name):
        return self.query


@pytest.fixture
def patch_db_service_client(monkeypatch):
    """Stub jobpipe.db's lazy SERVICE client (rescore uses it post-RLS).

    Mirrors conftest's patch_db_client: clear any real module attribute
    that would shadow __getattr__, then patch the lazy cache.
    """
    import jobpipe.db as db

    def _patch(fake):
        if "service_client" in vars(db):
            monkeypatch.delattr(db, "service_client")
        monkeypatch.setattr(db, "_service_client", fake)

    return _patch


# ── fetch_rescorable guards ─────────────────────────────────────────────


def test_fetch_rejects_active_statuses() -> None:
    for status in ("approved", "preparing", "applied", "needs_review", "failed"):
        with pytest.raises(ValueError):
            rescore.fetch_rescorable(status=status)


def test_fetch_filters_status_and_applies_since(patch_db_service_client) -> None:
    rows = [
        {"id": "a", "status": "new", "description": "x"},
        {"id": "b", "status": "approved", "description": "y"},  # sneaks past query
    ]
    fake = _FakeClient(rows)
    patch_db_service_client(fake)
    out = rescore.fetch_rescorable(status="new", since_days=30)
    assert [r["id"] for r in out] == ["a"]
    assert ("status", "new") in fake.query.eq_calls
    assert fake.query.gte_calls and fake.query.gte_calls[0][0] == "created_at"


# ── dry-run cost math ───────────────────────────────────────────────────


def test_estimate_cost_math(tmp_profile, monkeypatch) -> None:
    tmp_profile(overrides={"thesis.md": THESIS_FIXTURE})
    import jobpipe.hunt.prompts as prompts

    monkeypatch.setattr(prompts, "_PROFILE_CACHE", None)
    monkeypatch.setattr(rescore, "INPUT_USD_PER_MTOK", 5.0)
    monkeypatch.setattr(rescore, "OUTPUT_USD_PER_MTOK", 25.0)

    rows = [{"description": "d" * 4000}, {"description": "d" * 2000}]
    est = rescore.estimate_cost(rows)

    assert est["rows"] == 2
    fixed = est["system_tokens_per_row"] + est["profile_tokens_per_row"] + 60
    assert est["input_tokens"] == 2 * fixed + 1000 + 500
    assert est["output_tokens"] == 2 * rescore.EST_OUTPUT_TOKENS_PER_ROW
    expected = (
        est["input_tokens"] / 1e6 * 5.0 + est["output_tokens"] / 1e6 * 25.0
    )
    assert est["estimated_usd"] == round(expected, 2)
    # The fixed prompt must include the real scorer system prompt and the
    # thesis-bearing profile — both are thousands of chars.
    assert est["system_tokens_per_row"] > 500
    assert est["profile_tokens_per_row"] > 100


def test_estimate_cost_zero_rows(tmp_profile, monkeypatch) -> None:
    tmp_profile(overrides={"thesis.md": THESIS_FIXTURE})
    import jobpipe.hunt.prompts as prompts

    monkeypatch.setattr(prompts, "_PROFILE_CACHE", None)
    est = rescore.estimate_cost([])
    assert est["rows"] == 0
    assert est["input_tokens"] == 0
    assert est["output_tokens"] == 0
    assert est["estimated_usd"] == 0.0


def test_main_dry_run_never_scores_or_writes(
    tmp_profile, monkeypatch, patch_db_service_client, capsys
) -> None:
    tmp_profile(overrides={"thesis.md": THESIS_FIXTURE})
    import jobpipe.hunt.prompts as prompts

    monkeypatch.setattr(prompts, "_PROFILE_CACHE", None)
    patch_db_service_client(_FakeClient([{"id": "a", "status": "new", "description": "x"}]))

    def _boom(**_kw):
        raise AssertionError("score_job must not be called in dry-run")

    import jobpipe.hunt.scorer as scorer

    monkeypatch.setattr(scorer, "score_job", _boom)
    rescore.main(status="new", since_days=None, execute=False)
    out = capsys.readouterr().out
    assert "eligible rows: 1" in out
    assert "dry-run" in out
    assert "estimated cost" in out


# ── execute path: writes + optimistic status guard ──────────────────────


def _fake_score(**_kw):
    return {
        "score": 9,
        "tier": 1.5,
        "degree_gated": False,
        "reasoning": "r",
        "recommended_action": "notify",
        "legitimacy": "high_confidence",
        "legitimacy_reasoning": "lr",
    }


def test_run_rescore_writes_and_stamps(patch_db_service_client, monkeypatch) -> None:
    fake = _FakeClient([{"id": "a", "status": "new"}])
    patch_db_service_client(fake)
    import jobpipe.hunt.scorer as scorer

    monkeypatch.setattr(scorer, "score_job", _fake_score)
    stats = rescore.run_rescore([{"id": "a", "status": "new", "title": "t",
                                  "company": "c", "description": "d",
                                  "location": "Remote"}])
    assert stats == {"scored": 1, "written": 1,
                     "skipped_status_changed": 0, "errors": 0}
    payload = fake.query.update_payload
    assert payload["tier"] == 1.5
    assert payload["degree_gated"] is False
    assert payload["rescored_at"]
    # Optimistic guard: update constrained on id AND original status.
    assert ("id", "a") in fake.query.eq_calls
    assert ("status", "new") in fake.query.eq_calls


def test_run_rescore_skips_row_whose_status_moved(
    patch_db_service_client, monkeypatch
) -> None:
    # Backing row is now approved — the guarded update matches nothing.
    fake = _FakeClient([{"id": "a", "status": "approved"}])
    patch_db_service_client(fake)
    import jobpipe.hunt.scorer as scorer

    monkeypatch.setattr(scorer, "score_job", _fake_score)
    stats = rescore.run_rescore([{"id": "a", "status": "new", "title": "t",
                                  "company": "c", "description": "d",
                                  "location": "Remote"}])
    assert stats["written"] == 0
    assert stats["skipped_status_changed"] == 1


def test_run_rescore_batches_progress(patch_db_service_client, monkeypatch, capsys) -> None:
    fake = _FakeClient([{"id": "a", "status": "new"}])
    patch_db_service_client(fake)
    import jobpipe.hunt.scorer as scorer

    monkeypatch.setattr(scorer, "score_job", _fake_score)
    rows = [{"id": f"r{i}", "status": "new", "title": "t", "company": "c",
             "description": "d", "location": "Remote"} for i in range(5)]
    rescore.run_rescore(rows, batch_size=2)
    out = capsys.readouterr().out
    assert out.count("[rescore]") == math.ceil(5 / 2)
    assert "5/5 rows processed" in out
