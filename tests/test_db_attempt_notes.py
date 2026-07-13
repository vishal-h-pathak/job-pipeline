"""tests/test_db_attempt_notes.py — submission_log append + attempt-truth
merge semantics (P0 hygiene #3 / #1).

``jobpipe.db.record_prefill_verification`` used to overwrite the whole
``jobs.submission_log`` column on every call, silently destroying a prior
attempt's verification the moment a retry re-filled the same job.
``jobpipe.db.record_attempt_truth`` is new: it appends a ``truth`` key onto
an ``application_attempts`` row's ``notes`` without clobbering whatever
``close_attempt`` already wrote there.

Both are exercised against a minimal fake Supabase client — no network, no
real Postgres — using the same ``patch_db_client`` fixture pattern as
``tests/test_status_contract.py``.
"""

from __future__ import annotations

import jobpipe.db as db


class _FakeTable:
    """One fake table: seeded rows + last-write recording."""

    def __init__(self, rows: dict[int | str, dict]):
        self._rows = rows
        self._filter_id = None
        self.updates: list[tuple[object, dict]] = []

    def select(self, *_a, **_kw):
        return self

    def eq(self, _col, value):
        self._filter_id = value
        return self

    def update(self, payload):
        self.updates.append((self._filter_id, payload))
        # Reflect the write so a second call in the same test sees it.
        row = self._rows.setdefault(self._filter_id, {})
        row.update(payload)
        return self

    def execute(self):
        row = self._rows.get(self._filter_id)

        class _Result:
            data = [row] if row is not None else []

        return _Result()


class _FakeClient:
    def __init__(self, jobs: dict | None = None, attempts: dict | None = None):
        self._tables = {
            "jobs": _FakeTable(jobs or {}),
            "application_attempts": _FakeTable(attempts or {}),
        }

    def table(self, name):
        return self._tables[name]


# ── record_prefill_verification: append/merge keyed by attempt_n ──────────

def test_record_prefill_verification_first_write_seeds_attempts_and_verification(
    patch_db_client,
):
    fake = _FakeClient(jobs={"job-1": {"submission_log": None}})
    patch_db_client(fake)

    db.record_prefill_verification("job-1", {"summary": "filled 4 of 5"}, attempt_n=1)

    log = fake._tables["jobs"]._rows["job-1"]["submission_log"]
    assert log["verification"] == {"summary": "filled 4 of 5"}
    assert log["attempts"] == {"1": {"summary": "filled 4 of 5"}}


def test_record_prefill_verification_second_attempt_preserves_first(patch_db_client):
    seeded_log = {
        "verification": {"summary": "filled 4 of 5"},
        "attempts": {"1": {"summary": "filled 4 of 5"}},
    }
    fake = _FakeClient(jobs={"job-1": {"submission_log": seeded_log}})
    patch_db_client(fake)

    db.record_prefill_verification("job-1", {"summary": "filled 5 of 5"}, attempt_n=2)

    log = fake._tables["jobs"]._rows["job-1"]["submission_log"]
    # Latest verification at the top level (cockpit back-compat)...
    assert log["verification"] == {"summary": "filled 5 of 5"}
    # ...but attempt 1's history survives instead of being clobbered.
    assert log["attempts"]["1"] == {"summary": "filled 4 of 5"}
    assert log["attempts"]["2"] == {"summary": "filled 5 of 5"}


def test_record_prefill_verification_preserves_other_top_level_keys(patch_db_client):
    """A key some other writer put on submission_log (not ``verification`` /
    ``attempts``) must survive the merge."""
    seeded_log = {"some_other_writer_key": "keep me"}
    fake = _FakeClient(jobs={"job-1": {"submission_log": seeded_log}})
    patch_db_client(fake)

    db.record_prefill_verification("job-1", {"summary": "x"}, attempt_n=1)

    log = fake._tables["jobs"]._rows["job-1"]["submission_log"]
    assert log["some_other_writer_key"] == "keep me"


def test_record_prefill_verification_without_attempt_n_uses_latest_key(patch_db_client):
    fake = _FakeClient(jobs={"job-1": {"submission_log": None}})
    patch_db_client(fake)

    db.record_prefill_verification("job-1", {"summary": "x"})

    log = fake._tables["jobs"]._rows["job-1"]["submission_log"]
    assert log["attempts"] == {"latest": {"summary": "x"}}


def test_record_prefill_verification_swallows_client_exception(patch_db_client):
    class _BoomClient:
        def table(self, _name):
            raise RuntimeError("supabase is down")

    patch_db_client(_BoomClient())
    # Must not raise — the pre-fill path has already left a reviewable tab
    # open; a logging write hiccup can't be allowed to blow up the caller.
    db.record_prefill_verification("job-1", {"summary": "x"}, attempt_n=1)


# ── record_attempt_truth: merge a `truth` key onto attempts.notes ──────────

def test_record_attempt_truth_adds_truth_key_to_existing_notes(patch_db_client):
    fake = _FakeClient(attempts={
        7: {"notes": {"filled_fields": ["First Name"], "notes": "Filled 1 field"}},
    })
    patch_db_client(fake)

    db.record_attempt_truth(7, {"final_url": "https://x/thanks", "success_signal": {"kind": "url_redirect"}})

    notes = fake._tables["application_attempts"]._rows[7]["notes"]
    assert notes["filled_fields"] == ["First Name"]
    assert notes["notes"] == "Filled 1 field"
    assert notes["truth"]["final_url"] == "https://x/thanks"


def test_record_attempt_truth_handles_missing_notes(patch_db_client):
    fake = _FakeClient(attempts={7: {"notes": None}})
    patch_db_client(fake)

    db.record_attempt_truth(7, {"final_url": "https://x"})

    notes = fake._tables["application_attempts"]._rows[7]["notes"]
    assert notes == {"truth": {"final_url": "https://x"}}


def test_record_attempt_truth_swallows_client_exception(patch_db_client):
    class _BoomClient:
        def table(self, _name):
            raise RuntimeError("supabase is down")

    patch_db_client(_BoomClient())
    db.record_attempt_truth(7, {"final_url": "https://x"})
