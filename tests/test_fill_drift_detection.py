"""tests/test_fill_drift_detection.py — Task 4 drift-detection aggregator.

Exercises ``jobpipe.submit.scripts.detect_fill_drift`` against a fake
Supabase ``application_attempts`` table (same fake-client shape as
``tests/test_db_attempt_notes.py``, extended with the ``order`` / ``limit``
/ ``lt`` chain the real aggregator query uses) and a fake
``create_notification`` so no network call happens.

Three cases per the task brief:
  1. normal — current rate close to baseline -> no notification.
  2. real drift — current rate well below baseline -> notification fires
     with the exact numbers the aggregation computed.
  3. sparse data — not enough history yet -> no crash, no notification.
"""

from __future__ import annotations

import jobpipe.submit.scripts.detect_fill_drift as drift


class _FakeAttemptsTable:
    """One fake ``application_attempts`` table supporting the
    select/eq/lt/order/limit/execute chain ``fetch_recent_attempts`` uses.
    """

    def __init__(self, rows: list[dict]):
        self._all_rows = rows
        self._filtered = list(rows)
        self._order_col = None
        self._desc = False
        self._limit = None

    def select(self, *_a, **_kw):
        self._filtered = list(self._all_rows)
        self._order_col = None
        self._desc = False
        self._limit = None
        return self

    def eq(self, col, value):
        self._filtered = [r for r in self._filtered if r.get(col) == value]
        return self

    def lt(self, col, value):
        self._filtered = [
            r for r in self._filtered if r.get(col) is not None and r[col] < value
        ]
        return self

    def order(self, col, desc=False):
        self._order_col = col
        self._desc = desc
        return self

    def limit(self, n):
        self._limit = n
        return self

    def execute(self):
        rows = list(self._filtered)
        if self._order_col:
            rows.sort(key=lambda r: r.get(self._order_col), reverse=self._desc)
        if self._limit is not None:
            rows = rows[: self._limit]

        class _Result:
            data = rows

        return _Result()


class _FakeClient:
    def __init__(self, attempts: list[dict]):
        self._table = _FakeAttemptsTable(attempts)

    def table(self, name):
        assert name == "application_attempts"
        return self._table


def _row(row_id: int, *, adapter: str, fill_report: list[dict]) -> dict:
    return {
        "id": row_id,
        "adapter": adapter,
        "notes": {"fill_report": fill_report},
    }


def _fields(n: int, n_verified: int) -> list[dict]:
    """``n`` attempted field-spec entries, the first ``n_verified`` of them
    verified — mirrors ``apply_field_map``'s ``fill_report`` shape."""
    return [
        {"key": f"f{i}", "attempted": True, "value_verified": i < n_verified}
        for i in range(n)
    ]


class _FakeNotify:
    def __init__(self):
        self.calls: list[tuple] = []

    def __call__(self, notification_type, job, message=""):
        self.calls.append((notification_type, job, message))
        return True


def test_normal_case_no_notification(patch_db_client):
    """Current rate (90%) is within DEFAULT_DROP_THRESHOLD_PP (15pp) of the
    baseline (100%) -> no notification."""
    rows = []
    # Baseline: ids 1-10, every attempt's single field verified (100%).
    for i in range(1, 11):
        rows.append(_row(i, adapter="greenhouse", fill_report=_fields(1, 1)))
    # Current: ids 11-20, 9/10 attempts verified (90%).
    for i in range(11, 21):
        verified = 1 if i < 20 else 0
        rows.append(_row(i, adapter="greenhouse", fill_report=_fields(1, verified)))

    fake_client = _FakeClient(rows)
    patch_db_client(fake_client)
    fake_notify = _FakeNotify()
    original_notify = drift.create_notification
    drift.create_notification = fake_notify
    try:
        result = drift.check_drift_for_ats("greenhouse")
    finally:
        drift.create_notification = original_notify

    assert result is not None
    assert result["notified"] is False
    assert result["current_rate"] == 0.9
    assert result["baseline_rate"] == 1.0
    assert fake_notify.calls == []


def test_real_drift_fires_notification_with_correct_numbers(patch_db_client):
    """Current rate 52%, baseline 91% -> 39pp drop clears the 15pp
    threshold; the notification message carries those exact percentages
    (the brief's own worked example: 'Greenhouse fill rate 52% over last
    10 attempts (baseline 91%) — selectors likely drifted')."""
    rows = []
    # Baseline: ids 1-10, 10 fields each, 91/100 verified overall.
    # 9 rows fully verified (90) + 1 row with 1/10 verified (1) = 91.
    for i in range(1, 10):
        rows.append(_row(i, adapter="greenhouse", fill_report=_fields(10, 10)))
    rows.append(_row(10, adapter="greenhouse", fill_report=_fields(10, 1)))

    # Current: ids 11-20, 10 fields each, 52/100 verified overall.
    # 5 rows fully verified (50) + 1 row with 2/10 verified (2) + 4 rows
    # with 0/10 verified (0) = 52.
    for i in range(11, 16):
        rows.append(_row(i, adapter="greenhouse", fill_report=_fields(10, 10)))
    rows.append(_row(16, adapter="greenhouse", fill_report=_fields(10, 2)))
    for i in range(17, 21):
        rows.append(_row(i, adapter="greenhouse", fill_report=_fields(10, 0)))

    fake_client = _FakeClient(rows)
    patch_db_client(fake_client)
    fake_notify = _FakeNotify()
    original_notify = drift.create_notification
    drift.create_notification = fake_notify
    try:
        result = drift.check_drift_for_ats("greenhouse")
    finally:
        drift.create_notification = original_notify

    assert result is not None
    assert result["current_rate"] == 0.52
    assert result["baseline_rate"] == 0.91
    assert result["drop_pp"] == 39.0
    assert result["notified"] is True
    assert len(fake_notify.calls) == 1
    ntype, job, message = fake_notify.calls[0]
    assert ntype == "failed"
    assert job["id"] is None
    assert job["company"] == "fill-drift-detector"
    assert message == (
        "Greenhouse fill rate 52% over last 10 attempts (baseline 91%) "
        "— selectors likely drifted"
    )


def test_sparse_current_window_no_crash_no_notification(patch_db_client):
    """Only 3 attempts exist total (fewer than the default window of 10) ->
    skipped, not evaluated at all."""
    rows = [
        _row(i, adapter="lever", fill_report=_fields(1, 1)) for i in range(1, 4)
    ]
    fake_client = _FakeClient(rows)
    patch_db_client(fake_client)
    fake_notify = _FakeNotify()
    original_notify = drift.create_notification
    drift.create_notification = fake_notify
    try:
        result = drift.check_drift_for_ats("lever")
    finally:
        drift.create_notification = original_notify

    assert result is None
    assert fake_notify.calls == []


def test_sparse_missing_baseline_window_no_crash_no_notification(patch_db_client):
    """Exactly ``window`` current attempts exist but nothing precedes them
    (no baseline) -> skipped, not a false positive."""
    rows = [
        _row(i, adapter="ashby", fill_report=_fields(1, 1)) for i in range(1, 11)
    ]
    fake_client = _FakeClient(rows)
    patch_db_client(fake_client)
    fake_notify = _FakeNotify()
    original_notify = drift.create_notification
    drift.create_notification = fake_notify
    try:
        result = drift.check_drift_for_ats("ashby")
    finally:
        drift.create_notification = original_notify

    assert result is None
    assert fake_notify.calls == []


def test_no_attempted_fields_in_window_no_crash_no_notification(patch_db_client):
    """20 attempts exist (enough rows for both windows) but nothing in them
    was ever attempted (e.g. every optional field left blank) -> the rate
    is undefined, not zero, so this must not read as "100% drift.\""""
    empty_fields = [
        {"key": "linkedin", "attempted": False, "value_verified": False}
    ]
    rows = [
        _row(i, adapter="universal", fill_report=empty_fields) for i in range(1, 21)
    ]
    fake_client = _FakeClient(rows)
    patch_db_client(fake_client)
    fake_notify = _FakeNotify()
    original_notify = drift.create_notification
    drift.create_notification = fake_notify
    try:
        result = drift.check_drift_for_ats("universal")
    finally:
        drift.create_notification = original_notify

    assert result is None
    assert fake_notify.calls == []


def test_detect_fill_drift_sweeps_multiple_ats_and_skips_failures(patch_db_client):
    """The top-level sweep evaluates every ATS independently: one with real
    drift, one sparse, and (via a fetch that raises for a third) confirms a
    single ATS's failure doesn't sink the whole run."""
    rows = []
    for i in range(1, 10):
        rows.append(_row(i, adapter="greenhouse", fill_report=_fields(10, 10)))
    rows.append(_row(10, adapter="greenhouse", fill_report=_fields(10, 1)))
    for i in range(11, 16):
        rows.append(_row(i, adapter="greenhouse", fill_report=_fields(10, 10)))
    rows.append(_row(16, adapter="greenhouse", fill_report=_fields(10, 2)))
    for i in range(17, 21):
        rows.append(_row(i, adapter="greenhouse", fill_report=_fields(10, 0)))
    # lever: sparse, only 2 attempts.
    rows.append(_row(101, adapter="lever", fill_report=_fields(1, 1)))
    rows.append(_row(102, adapter="lever", fill_report=_fields(1, 1)))

    fake_client = _FakeClient(rows)
    patch_db_client(fake_client)
    fake_notify = _FakeNotify()
    original_notify = drift.create_notification
    drift.create_notification = fake_notify

    def _boom_fetch(ats, *, limit, before_id=None):
        if ats == "ashby":
            raise RuntimeError("transient DB error")
        return drift.fetch_recent_attempts(ats, limit=limit, before_id=before_id)

    try:
        results = drift.detect_fill_drift(
            ats_list=("greenhouse", "lever", "ashby"), fetch=_boom_fetch,
        )
    finally:
        drift.create_notification = original_notify

    # Only greenhouse had enough history on both windows; lever was sparse
    # (omitted, not a None entry) and ashby's fetch blew up (also omitted).
    assert len(results) == 1
    assert results[0]["ats"] == "greenhouse"
    assert results[0]["notified"] is True
    assert len(fake_notify.calls) == 1
