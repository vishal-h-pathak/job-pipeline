"""P2 (callback feedback loop) — jobpipe.db.mark_applied materials retention.

Before P2, "Mark Applied" deleted the generated resume/cover-letter PDFs
from Storage by default (``clear_materials=True``). That destroyed the
exact artifact the resume-variant x company-type callback loop needs
later to correlate materials with replies. P2 flips the default to
False; this test pins the new default and confirms the opt-in True path
still works for callers that genuinely want to reclaim storage.

Mirrors the ``tailor_db`` fixture pattern in test_mark_failed_split.py —
a stubbed supabase client plus a synthetic ``storage`` module so
``mark_applied``'s lazy ``from storage import delete_all_for_job`` can
be observed without a real Storage round-trip.
"""

from __future__ import annotations

import sys

import pytest


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
def applied_db(monkeypatch):
    fake = _FakeClient()
    import supabase  # type: ignore[import-not-found]
    monkeypatch.setattr(supabase, "create_client", lambda *a, **kw: fake)

    import jobpipe.db as db
    monkeypatch.setattr(db, "_client", None)
    monkeypatch.setattr(db, "_service_client", None)
    monkeypatch.setattr(db, "SUPABASE_SERVICE_ROLE_KEY", "service-test")

    storage_stub = type(sys)("storage")
    delete_calls: list[str] = []
    storage_stub.delete_all_for_job = lambda jid: delete_calls.append(jid)
    monkeypatch.setitem(sys.modules, "storage", storage_stub)

    db.client = fake
    yield db, fake, delete_calls


def test_mark_applied_default_keeps_materials(applied_db):
    db, fake, delete_calls = applied_db
    db.mark_applied("job-123", application_notes="submitted via cockpit")

    payload = fake.calls["update_payload"]
    assert payload["status"] == "applied"
    assert "applied_at" in payload and "submitted_at" in payload
    # The default no longer clears materials — no Storage delete, no
    # nulled path columns.
    assert delete_calls == []
    assert "resume_pdf_path" not in payload
    assert "cover_letter_pdf_path" not in payload


def test_mark_applied_explicit_clear_materials_still_deletes(applied_db):
    db, fake, delete_calls = applied_db
    db.mark_applied("job-456", clear_materials=True)

    payload = fake.calls["update_payload"]
    assert delete_calls == ["job-456"]
    assert payload["resume_pdf_path"] is None
    assert payload["cover_letter_pdf_path"] is None


def test_mark_applied_default_param_value_is_false():
    """Pin the signature default directly — belt-and-suspenders against
    a future edit silently flipping it back."""
    import inspect
    import jobpipe.db as db

    sig = inspect.signature(db.mark_applied)
    assert sig.parameters["clear_materials"].default is False
