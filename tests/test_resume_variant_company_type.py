"""P2 (callback feedback loop) — resume_variant + company_type recording.

Two write sites:
  - jobpipe.db.mark_ready_for_review persists resume_variant (the
    latex_resume.py style lane) + ats_qa (P2's QA-gate verdict).
  - jobpipe.db.upsert_job persists company_type from the hunt scorer's
    result dict, on both the insert and update branches.

Plus the hunt scorer's normalization: company_type always comes back as
one of the closed taxonomy values, defaulting to "other" for anything
the model didn't emit or emitted wrong.
"""

from __future__ import annotations

import pytest

from jobpipe.hunt import scorer


class _FakeUpdateChain:
    def __init__(self, recorder):
        self._recorder = recorder

    def update(self, payload):
        self._recorder["update_payload"] = payload
        return self

    def eq(self, col, val):
        self._recorder["eq"] = (col, val)
        return self

    def execute(self):
        return type("R", (), {"data": [self._recorder.get("update_payload", {})]})()


class _FakeClient:
    def __init__(self):
        self.calls: dict = {}

    def table(self, name):
        self.calls["table"] = name
        return _FakeUpdateChain(self.calls)


@pytest.fixture
def stub_db(monkeypatch):
    fake = _FakeClient()
    import supabase  # type: ignore[import-not-found]
    monkeypatch.setattr(supabase, "create_client", lambda *a, **kw: fake)

    import jobpipe.db as db
    monkeypatch.setattr(db, "_client", None)
    monkeypatch.setattr(db, "_service_client", None)
    monkeypatch.setattr(db, "SUPABASE_SERVICE_ROLE_KEY", "service-test")
    db.client = fake
    yield db, fake


# ── mark_ready_for_review: resume_variant + ats_qa ─────────────────────────


def test_mark_ready_for_review_persists_resume_variant(stub_db):
    db, fake = stub_db
    db.mark_ready_for_review("job-1", resume_variant="modern")
    payload = fake.calls["update_payload"]
    assert payload["resume_variant"] == "modern"


def test_mark_ready_for_review_persists_ats_qa_dict(stub_db):
    db, fake = stub_db
    qa = {
        "top_keywords": ["python", "agents"],
        "missing": ["kubernetes"],
        "ats_score": 72,
        "highest_impact_fix": "add a metrics bullet",
        "robotic_bullets": [],
    }
    db.mark_ready_for_review("job-2", ats_qa=qa)
    payload = fake.calls["update_payload"]
    assert payload["ats_qa"] == qa


def test_mark_ready_for_review_omits_unset_variant_and_qa(stub_db):
    """No resume_variant/ats_qa passed -> keys absent, not written as
    None (matches the existing extras-dict convention for every other
    optional kwarg on this function)."""
    db, fake = stub_db
    db.mark_ready_for_review("job-3", resume_path="r")
    payload = fake.calls["update_payload"]
    assert "resume_variant" not in payload
    assert "ats_qa" not in payload


# ── upsert_job: company_type on insert + update ────────────────────────────


class _UpsertFakeQuery:
    def __init__(self, existing: list[dict]):
        self._existing = existing
        self.insert_payload = None
        self.update_payload = None
        self._mode = None

    def select(self, _cols):
        self._mode = "select"
        return self

    def insert(self, payload, **kw):
        self._mode = "insert"
        self.insert_payload = payload
        return self

    def upsert(self, payload, **kw):
        self._mode = "insert"
        self.insert_payload = payload
        return self

    def update(self, payload):
        self._mode = "update"
        self.update_payload = payload
        return self

    def eq(self, _col, _val):
        return self

    def execute(self):
        if self._mode == "select":
            return type("R", (), {"data": list(self._existing)})()
        return type("R", (), {"data": []})()


class _UpsertFakeClient:
    def __init__(self, existing: list[dict] | None = None):
        self.query = _UpsertFakeQuery(existing or [])

    def table(self, _name):
        return self.query


def test_upsert_job_insert_writes_company_type(patch_db_client):
    fake = _UpsertFakeClient(existing=[])
    patch_db_client(fake)

    import jobpipe.db as db
    db.upsert_job(
        {"id": "j1", "title": "t", "company": "Acme"},
        {"score": 8, "company_type": "ai_startup"},
    )
    assert fake.query.insert_payload["company_type"] == "ai_startup"


def test_upsert_job_update_writes_company_type(patch_db_client):
    fake = _UpsertFakeClient(existing=[{"id": "j1"}])
    patch_db_client(fake)

    import jobpipe.db as db
    db.upsert_job(
        {"id": "j1", "title": "t", "company": "Acme"},
        {"score": 8, "company_type": "enterprise"},
    )
    assert fake.query.update_payload["company_type"] == "enterprise"


# ── hunt scorer: company_type normalization ────────────────────────────────


def test_score_job_normalizes_valid_company_type(monkeypatch):
    monkeypatch.setattr(
        scorer.llm, "complete",
        lambda **_: (
            '{"score": 7, "tier": 1, "reasoning": "ok", '
            '"recommended_action": "notify", "legitimacy": "high_confidence", '
            '"company_type": "frontier_lab"}'
        ),
    )
    monkeypatch.setattr(scorer, "build_profile_prompt_string", lambda: "P")
    monkeypatch.setattr(scorer, "_system", lambda: "S")

    result = scorer.score_job("Researcher", "Anthropic", "desc", "Remote")
    assert result["company_type"] == "frontier_lab"


@pytest.mark.parametrize("raw", [None, "", "not_a_real_bucket", "  "])
def test_score_job_defaults_company_type_to_other(monkeypatch, raw):
    import json as _json

    payload = {
        "score": 7, "tier": 1, "reasoning": "ok",
        "recommended_action": "notify", "legitimacy": "high_confidence",
    }
    if raw is not None:
        payload["company_type"] = raw
    body = _json.dumps(payload)
    monkeypatch.setattr(scorer.llm, "complete", lambda **_: body)
    monkeypatch.setattr(scorer, "build_profile_prompt_string", lambda: "P")
    monkeypatch.setattr(scorer, "_system", lambda: "S")

    result = scorer.score_job("Eng", "Co", "desc", "Remote")
    assert result["company_type"] == "other"
