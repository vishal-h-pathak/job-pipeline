"""Tests for jobpipe.tailor.manual.upsert.upsert_manual_job.

The Supabase client is faked via a chainable mock that records the
final ``insert(...)`` / ``update(...)`` payload. Patches
``jobpipe.db.client`` directly — the module's ``__getattr__`` honours
an explicit assignment.
"""

from __future__ import annotations

from unittest.mock import MagicMock

import pytest

from jobpipe.tailor.manual import ScrapedPosting


# ── Chainable Supabase double ────────────────────────────────────────────

class _FakeQuery:
    """Mimics ``client.table(...).select|insert|update|eq|execute()``."""

    def __init__(self, existing_rows: list[dict]):
        self._existing = existing_rows
        self.insert_payload: dict | None = None
        self.update_payload: dict | None = None
        # Defaults — overridden per-test where needed
        self._mode = None

    def select(self, _cols):
        self._mode = "select"
        return self

    def insert(self, payload):
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
            return MagicMock(data=list(self._existing))
        return MagicMock(data=[])


class _FakeClient:
    def __init__(self, existing_rows: list[dict] | None = None):
        self.query = _FakeQuery(existing_rows or [])

    def table(self, _name):
        return self.query


@pytest.fixture
def db_with_no_existing(monkeypatch):
    import jobpipe.db
    fake = _FakeClient(existing_rows=[])
    monkeypatch.setattr(jobpipe.db, "client", fake, raising=False)
    return fake


@pytest.fixture
def db_with_existing(request, monkeypatch):
    """Parametrize the existing row's status via the ``status`` indirect param."""
    status = getattr(request, "param", "new")
    import jobpipe.db
    fake = _FakeClient(
        existing_rows=[{"id": "PLACEHOLDER", "status": status}]
    )
    monkeypatch.setattr(jobpipe.db, "client", fake, raising=False)
    return fake


def _posting(confidence: str = "high", title: str = "ML Researcher",
             company: str = "Anthropic") -> ScrapedPosting:
    return ScrapedPosting(
        url="https://job-boards.greenhouse.io/anthropic/jobs/4123456",
        title=title,
        company=company,
        location="San Francisco, CA",
        description="We are looking for an ML researcher.",
        ats_kind="greenhouse",
        confidence=confidence,  # type: ignore[arg-type]
    )


# ── Tests ────────────────────────────────────────────────────────────────

def test_upsert_high_confidence_inserts_status_approved(db_with_no_existing):
    from jobpipe.tailor.manual.upsert import upsert_manual_job

    job_id, final_status = upsert_manual_job(_posting(confidence="high"))

    assert final_status == "approved"
    assert len(job_id) == 16  # make_job_id sha1[:16]
    payload = db_with_no_existing.query.insert_payload
    assert payload is not None
    assert payload["status"] == "approved"
    assert payload["source"] == "manual"
    assert payload["ats_kind"] == "greenhouse"
    assert payload["title"] == "ML Researcher"
    assert payload["company"] == "Anthropic"
    assert payload["id"] == job_id
    # Update path NOT taken:
    assert db_with_no_existing.query.update_payload is None


def test_upsert_low_confidence_inserts_status_discovered(db_with_no_existing):
    """Amendment 1: low-confidence rows land at 'discovered', no tailor."""
    from jobpipe.tailor.manual.upsert import upsert_manual_job

    _, final_status = upsert_manual_job(
        _posting(confidence="low", title="Embedded Engineer", company="Smallco")
    )

    assert final_status == "discovered"
    payload = db_with_no_existing.query.insert_payload
    assert payload["status"] == "discovered"
    assert payload["source"] == "manual"


def test_upsert_is_deterministic_on_make_job_id():
    """Same posting → same job_id (across processes)."""
    from jobpipe.shared.jobid import make_job_id

    posting = _posting()
    expected = make_job_id(posting.url, posting.title, posting.company)

    # Stub a fresh client per call so the insert path runs cleanly.
    class _Stub:
        def __init__(self):
            self.query = _FakeQuery([])

        def table(self, _):
            return self.query

    import jobpipe.db
    for _ in range(2):
        jobpipe.db.client = _Stub()
        from jobpipe.tailor.manual.upsert import upsert_manual_job
        observed_id, _ = upsert_manual_job(posting)
        assert observed_id == expected


@pytest.mark.parametrize("safe_status", ["new", "discovered", "ignored", "expired"])
def test_upsert_overwrites_safe_existing_status_via_update_path(
    monkeypatch, safe_status,
):
    import jobpipe.db
    fake = _FakeClient(
        existing_rows=[{"id": "x", "status": safe_status}]
    )
    monkeypatch.setattr(jobpipe.db, "client", fake, raising=False)

    from jobpipe.tailor.manual.upsert import upsert_manual_job
    _, final_status = upsert_manual_job(_posting(confidence="high"))

    assert final_status == "approved"
    # UPDATE path taken, not INSERT
    assert fake.query.update_payload is not None
    assert fake.query.insert_payload is None
    # Update payload doesn't carry an `id` field (we strip it on UPDATE).
    assert "id" not in fake.query.update_payload
    assert fake.query.update_payload["status"] == "approved"
    assert fake.query.update_payload["source"] == "manual"


@pytest.mark.parametrize(
    "unsafe_status",
    ["approved", "preparing", "ready_for_review", "prefilling",
     "awaiting_human_submit", "applied", "failed", "skipped"],
)
def test_upsert_raises_collision_on_unsafe_existing_status(
    monkeypatch, unsafe_status,
):
    import jobpipe.db
    fake = _FakeClient(
        existing_rows=[{"id": "x", "status": unsafe_status}]
    )
    monkeypatch.setattr(jobpipe.db, "client", fake, raising=False)

    from jobpipe.tailor.manual.upsert import upsert_manual_job, CollisionError

    with pytest.raises(CollisionError) as exc:
        upsert_manual_job(_posting(confidence="high"))

    assert exc.value.existing_status == unsafe_status
    # No insert or update ran:
    assert fake.query.insert_payload is None
    assert fake.query.update_payload is None
