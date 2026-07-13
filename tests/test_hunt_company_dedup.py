"""P2 (callback feedback loop) — 30-day company dedup window at hunt upsert.

Two surfaces:
  - jobpipe.hunt.agent._tag_company_dedup — pure function, no I/O. Given
    a job dict and a normalized-company -> recent-rows lookup (the shape
    jobpipe.db.get_recent_company_activity returns, pre-grouped), tags
    duplicate_recent_company / reposting_of_job_id in place.
  - jobpipe.db.upsert_job — persists those two fields when the caller
    set them on the job dict (mirrors the existing link_fields pattern).

Fresh / within-window-different-role / within-window-same-role (reposting)
are the three cases the P2 prompt calls out explicitly.
"""

from __future__ import annotations

from collections import defaultdict

from jobpipe.hunt.agent import _tag_company_dedup
from jobpipe.shared.jobid import normalize_text


def _by_company(rows: list[dict]) -> dict[str, list[dict]]:
    grouped: dict[str, list[dict]] = defaultdict(list)
    for row in rows:
        grouped[normalize_text(row.get("company", ""))].append(row)
    return grouped


# ── _tag_company_dedup: three cases ─────────────────────────────────────


def test_fresh_company_no_recent_activity_sets_no_flags():
    job = {"company": "Brand New Co", "title": "ML Engineer"}
    _tag_company_dedup(job, recent_by_company={})
    assert "duplicate_recent_company" not in job
    assert "reposting_of_job_id" not in job


def test_within_window_different_role_sets_duplicate_flag():
    recent = _by_company([
        {"id": "prior-1", "company": "Acme Inc", "title": "Backend Engineer"},
    ])
    job = {"company": "Acme, Inc.", "title": "ML Engineer"}
    _tag_company_dedup(job, recent_by_company=recent)
    assert job.get("duplicate_recent_company") is True
    assert "reposting_of_job_id" not in job


def test_within_window_same_role_sets_reposting_link_not_duplicate_flag():
    """Same normalized company AND title -> reposting, which supersedes
    the plain duplicate flag (a stronger, more specific signal)."""
    recent = _by_company([
        {"id": "prior-2", "company": "Acme Inc", "title": "ML Engineer (Remote)"},
    ])
    job = {"company": "ACME INC", "title": "ML Engineer"}
    _tag_company_dedup(job, recent_by_company=recent)
    assert job.get("reposting_of_job_id") == "prior-2"
    assert "duplicate_recent_company" not in job


def test_company_name_normalization_collapses_punctuation_and_case():
    recent = _by_company([{"id": "p", "company": "Foo & Bar, LLC.", "title": "X"}])
    job = {"company": "foo bar llc", "title": "different role"}
    _tag_company_dedup(job, recent_by_company=recent)
    assert job.get("duplicate_recent_company") is True


def test_different_company_does_not_match():
    recent = _by_company([{"id": "p", "company": "Acme", "title": "ML Engineer"}])
    job = {"company": "Widgets Co", "title": "ML Engineer"}
    _tag_company_dedup(job, recent_by_company=recent)
    assert "duplicate_recent_company" not in job
    assert "reposting_of_job_id" not in job


def test_reposting_picks_first_matching_row_when_multiple_titles_present():
    recent = _by_company([
        {"id": "other-role", "company": "Acme", "title": "Backend Engineer"},
        {"id": "same-role", "company": "Acme", "title": "ML Engineer"},
    ])
    job = {"company": "Acme", "title": "ML Engineer"}
    _tag_company_dedup(job, recent_by_company=recent)
    assert job.get("reposting_of_job_id") == "same-role"


# ── upsert_job persists the dedup fields ────────────────────────────────


class _FakeQuery:
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


class _FakeClient:
    def __init__(self, existing: list[dict] | None = None):
        self.query = _FakeQuery(existing or [])

    def table(self, _name):
        return self.query


def test_upsert_job_insert_persists_duplicate_flag(patch_db_client):
    fake = _FakeClient(existing=[])
    patch_db_client(fake)

    import jobpipe.db as db
    db.upsert_job(
        {"id": "j1", "title": "t", "company": "Acme",
         "duplicate_recent_company": True},
        {"score": 8},
    )
    assert fake.query.insert_payload["duplicate_recent_company"] is True


def test_upsert_job_insert_persists_reposting_link(patch_db_client):
    fake = _FakeClient(existing=[])
    patch_db_client(fake)

    import jobpipe.db as db
    db.upsert_job(
        {"id": "j1", "title": "t", "company": "Acme",
         "reposting_of_job_id": "prior-9"},
        {"score": 8},
    )
    assert fake.query.insert_payload["reposting_of_job_id"] == "prior-9"


def test_upsert_job_omits_dedup_fields_when_unset(patch_db_client):
    fake = _FakeClient(existing=[])
    patch_db_client(fake)

    import jobpipe.db as db
    db.upsert_job({"id": "j1", "title": "t", "company": "Acme"}, {"score": 8})
    assert "duplicate_recent_company" not in fake.query.insert_payload
    assert "reposting_of_job_id" not in fake.query.insert_payload


def test_upsert_job_update_persists_duplicate_flag(patch_db_client):
    fake = _FakeClient(existing=[{"id": "j1"}])
    patch_db_client(fake)

    import jobpipe.db as db
    db.upsert_job(
        {"id": "j1", "title": "t", "company": "Acme",
         "duplicate_recent_company": True},
        {"score": 8},
    )
    assert fake.query.update_payload["duplicate_recent_company"] is True
