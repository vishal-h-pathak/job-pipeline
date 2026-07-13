"""tests/test_prefill_attempt_cap_timeout.py — Task 5 (P0 follow-up):
timeout cycles must not burn the attempt budget.

``jobpipe.tailor.pipeline._prefill_one_job``'s pre-flight max-attempts
check used to compare the raw ``next_attempt_n(job_id)`` counter (which
increments once per ``application_attempts`` row regardless of why the
row closed) against ``MAX_ATTEMPTS_PER_JOB``. A tab a human never looked
at times out in ``_wait_for_human_decision`` and re-queues the job to
``prefilling`` — but the next cycle's ``next_attempt_n`` is one higher
anyway, so three unattended decision-wait timeouts alone (no real fill
failure, ever) tripped the cap and permanently failed a job the human
never actually saw.

The fix: the cap comparison now reads ``count_attempts_toward_cap``,
which excludes rows ``mark_attempt_timeout`` flagged with
``notes.timeout = True``. ``next_attempt_n`` is untouched — it keeps
numbering ``application_attempts`` rows monotonically for
``open_attempt``.

This test drives the real ``jobpipe.db`` attempt-row functions
(``next_attempt_n``, ``count_attempts_toward_cap``, ``open_attempt``,
``close_attempt``) against a small in-memory fake Supabase table — only
the DB *client* is faked (via the shared ``patch_db_client`` fixture),
not the pipeline's attempt-accounting functions themselves — so the
pre-flight check in ``_prefill_one_job`` is exercised for real. Playwright,
the ATS applicant, URL resolution, and storage are stubbed the same way
``tests/test_prefill_attempts_audit.py`` stubs them, since this test cares
about the cap decision, not the fill mechanics.
"""

from __future__ import annotations

import sys
from types import SimpleNamespace

import pytest


# ── Required env (jobpipe.submit.config fail-louds without these) ──────────


@pytest.fixture(autouse=True)
def _required_env(monkeypatch):
    for k, v in {
        "SUPABASE_URL": "https://example.supabase.co",
        "SUPABASE_KEY": "anon-test",
        "SUPABASE_SERVICE_ROLE_KEY": "service-test",
        "BROWSERBASE_API_KEY": "bb-test",
        "BROWSERBASE_PROJECT_ID": "bb-proj-test",
        "ANTHROPIC_API_KEY": "sk-test",
        "HEADLESS": "1",
    }.items():
        monkeypatch.setenv(k, v)
    monkeypatch.delenv("JOBPIPE_BROWSER_CDP", raising=False)


# ── Fake Playwright surface (minimal — mirrors test_prefill_attempts_audit) ─


class _FakePage:
    def __init__(self):
        self.closed = False

    def goto(self, url, wait_until=None, timeout=None):
        pass

    def wait_for_load_state(self, *args, **kwargs):
        return None

    def screenshot(self, full_page=False):
        return b"\x89PNG_FAKE"

    def close(self):
        self.closed = True


class _FakeBrowser:
    def __init__(self, page):
        self._page = page

    def new_context(self, **kwargs):
        return SimpleNamespace(new_page=lambda: self._page)

    def close(self):
        return None


class _FakeChromium:
    def __init__(self, browser):
        self._browser = browser

    def launch(self, headless=False):
        return self._browser


class _FakePW:
    def __init__(self, page):
        self.chromium = _FakeChromium(_FakeBrowser(page))


class _SyncPlaywrightCM:
    def __init__(self, page):
        self._pw = _FakePW(page)

    def __enter__(self):
        return self._pw

    def __exit__(self, *exc):
        return False


def _install_fake_playwright(monkeypatch, page):
    fake_pw_pkg = type(sys)("playwright")
    fake_sync = type(sys)("playwright.sync_api")

    fake_sync.sync_playwright = lambda: _SyncPlaywrightCM(page)
    fake_sync.Page = type("Page", (), {})
    fake_sync.Browser = type("Browser", (), {})
    fake_sync.TimeoutError = type("TimeoutError", (Exception,), {})
    fake_pw_pkg.sync_api = fake_sync

    monkeypatch.setitem(sys.modules, "playwright", fake_pw_pkg)
    monkeypatch.setitem(sys.modules, "playwright.sync_api", fake_sync)


class _FakeApplicant:
    """A clean, always-succeeds greenhouse-shaped applicant."""

    name = "greenhouse"

    def fill_form(self, page, job, resume_path=None, cover_letter_path=None):
        return {
            "success": True,
            "fields_filled": ["First Name", "Email"],
            "notes": "Filled 2 fields",
            "screenshot_path": None,
            "fill_report": [
                {"key": "First Name", "label": "First Name", "type": "text",
                 "required": True, "attempted": True,
                 "matched_selector": 'input[name="first_name"]',
                 "value_verified": True, "misses": []},
            ],
        }


# ── Fake Supabase client: real jobpipe.db attempt functions run against it ─


class _FakeAttemptsTable:
    """Minimal chainable fake for the ``application_attempts`` table.

    Supports exactly the operations the real ``jobpipe.db`` attempt-row
    functions issue: ``select().eq().order().limit().execute()``
    (``next_attempt_n``), ``select().eq().execute()``
    (``count_attempts_toward_cap`` / ``mark_attempt_timeout``'s read),
    ``insert().execute()`` (``open_attempt``), and
    ``update().eq().execute()`` (``close_attempt`` / ``mark_attempt_timeout``'s
    write).
    """

    def __init__(self, seed_rows=None):
        self._rows = list(seed_rows or [])
        self._next_id = max((r["id"] for r in self._rows), default=0) + 1
        self._mode = None
        self._filters: list[tuple[str, object]] = []
        self._order_col = None
        self._order_desc = False
        self._limit_n = None
        self._insert_payload = None
        self._update_payload = None

    def select(self, *_cols, **_kw):
        self._mode = "select"
        self._filters = []
        self._order_col = None
        self._limit_n = None
        return self

    def eq(self, col, value):
        self._filters.append((col, value))
        return self

    def order(self, col, desc=False):
        self._order_col = col
        self._order_desc = desc
        return self

    def limit(self, n):
        self._limit_n = n
        return self

    def insert(self, payload):
        self._mode = "insert"
        self._insert_payload = payload
        return self

    def update(self, payload):
        self._mode = "update"
        self._update_payload = payload
        self._filters = []
        return self

    def _matches(self, row):
        return all(row.get(col) == val for col, val in self._filters)

    def execute(self):
        if self._mode == "insert":
            row = dict(self._insert_payload)
            row["id"] = self._next_id
            self._next_id += 1
            self._rows.append(row)

            class _Result:
                data = [row]

            return _Result()

        if self._mode == "update":
            matched = [r for r in self._rows if self._matches(r)]
            for r in matched:
                r.update(self._update_payload)

            class _Result:
                data = matched

            return _Result()

        matched = [r for r in self._rows if self._matches(r)]
        if self._order_col:
            matched = sorted(
                matched, key=lambda r: r.get(self._order_col, 0),
                reverse=self._order_desc,
            )
        if self._limit_n is not None:
            matched = matched[: self._limit_n]

        class _Result:
            data = matched

        return _Result()


class _FakeSupabaseClient:
    def __init__(self, attempts_rows=None):
        self._attempts = _FakeAttemptsTable(attempts_rows)

    def table(self, name):
        assert name == "application_attempts", (
            f"this test's fake client only backs application_attempts, got {name}"
        )
        return self._attempts


# ── Pipeline scaffolding — stub everything EXCEPT the attempt-accounting ───
# functions under test (next_attempt_n / count_attempts_toward_cap /
# open_attempt / close_attempt / mark_attempt_timeout stay real).


def _stub_pipeline_surface(monkeypatch, *, applicant, call_log, tmp_resume_path):
    from jobpipe.tailor import pipeline as p

    monkeypatch.setattr(
        p, "mark_awaiting_submit",
        lambda *a, **kw: call_log.append(("mark_awaiting_submit", a, kw)),
    )
    monkeypatch.setattr(
        p, "mark_tailor_failed",
        lambda *a, **kw: call_log.append(("mark_tailor_failed", a, kw)),
    )
    monkeypatch.setattr(
        p, "send_awaiting_submit",
        lambda *a, **kw: call_log.append(("send_awaiting_submit",)),
    )
    monkeypatch.setattr(
        p, "send_failed",
        lambda *a, **kw: call_log.append(("send_failed", a, kw)),
    )

    def _handoff_stub(page, job, reason, unfilled=None, summary=None):
        call_log.append(("handoff", reason))
        return {
            "handoff": True, "materials_dir": "/tmp/handoff/x",
            "checklist": [], "application_notes": "ASSISTED-MANUAL",
            "reason": reason, "problems": [],
        }

    monkeypatch.setattr(p, "assisted_manual_handoff", _handoff_stub)
    monkeypatch.setattr(
        p, "record_prefill_verification",
        lambda jid, v, **kw: call_log.append(("record_verification", jid)),
    )
    monkeypatch.setattr(p, "download_to_tmp", lambda key: tmp_resume_path)
    monkeypatch.setattr(
        p, "upload_prefill_screenshot",
        lambda jid, png_bytes: f"{jid}/prefill.png",
    )

    import jobpipe.shared.ats_detect as ats_mod
    monkeypatch.setattr(ats_mod, "detect_ats", lambda url: "greenhouse")
    monkeypatch.setattr(ats_mod, "get_applicant", lambda url: applicant)

    import url_resolver
    monkeypatch.setattr(
        url_resolver, "resolve_application_url",
        lambda url: {"resolved": url, "is_ats": True, "trail": [], "notes": "ok"},
    )

    # Stop-and-wait advance resolves immediately with a terminal decision —
    # this test is about the pre-flight cap check, not the wait loop.
    monkeypatch.setattr(
        p, "get_job",
        lambda jid: call_log.append(("wait_poll", jid)) or {"id": jid, "status": "applied"},
    )

    return p


def _make_job(job_id: str) -> dict:
    return {
        "id": job_id,
        "company": "TestCo",
        "title": "Test Engineer",
        "url": "https://boards.greenhouse.io/testco/jobs/1",
        "submission_url": "https://boards.greenhouse.io/testco/jobs/1",
        "application_url": "https://boards.greenhouse.io/testco/jobs/1",
        "resume_pdf_path": f"{job_id}/resume.pdf",
        "cover_letter_path": "Dear Team,\n\nI am writing about your role.",
        "form_answers": {"first_name": "Vishal"},
    }


@pytest.fixture
def tmp_resume_pdf(tmp_path):
    p = tmp_path / "fake_resume.pdf"
    p.write_bytes(b"%PDF-fake-for-cap-timeout-test")
    return p


# ── Tests ────────────────────────────────────────────────────────────────


def test_five_timeout_cycles_do_not_burn_the_attempt_budget(
    monkeypatch, patch_db_client, tmp_resume_pdf,
):
    """The brief's literal acceptance scenario: a job with 5 prior
    decision-wait-timeout attempt rows (MAX_ATTEMPTS_PER_JOB defaults to 3
    — well past it on the raw-count comparison) must still be pre-fillable
    on cycle 6, never routed to ``mark_tailor_failed``.
    """
    job_id = "cap-timeout-job"
    job = _make_job(job_id)

    # Seed 5 CLOSED, timeout-marked attempt rows — exactly what 5
    # consecutive _wait_for_human_decision timeouts leave behind:
    # open_attempt (in_progress) -> mark_attempt_timeout flags notes.timeout.
    seeded_rows = [
        {
            "id": i, "job_id": job_id, "attempt_n": i,
            "outcome": "in_progress", "notes": {"timeout": True},
        }
        for i in range(1, 6)
    ]
    fake_db = _FakeSupabaseClient(seeded_rows)
    patch_db_client(fake_db)

    call_log: list = []
    fake_page = _FakePage()
    _install_fake_playwright(monkeypatch, fake_page)

    p = _stub_pipeline_surface(
        monkeypatch, applicant=_FakeApplicant(), call_log=call_log,
        tmp_resume_path=tmp_resume_pdf,
    )
    monkeypatch.setattr(p, "get_prefill_requested_jobs", lambda: [job])

    p.process_prefill_requested_jobs()

    ops = [entry[0] for entry in call_log]

    # The cap did NOT trip: no bare pre-flight failure, the fill proceeded
    # and closed out cleanly.
    assert "mark_tailor_failed" not in ops, (
        f"5 timeout cycles wrongly burned the attempt budget; ops={ops}"
    )
    assert "send_failed" not in ops
    assert "wait_poll" in ops  # got all the way through to the wait loop

    # next_attempt_n's own numbering is untouched: cycle 6 gets attempt_n=6.
    new_rows = [r for r in fake_db._attempts._rows if r["attempt_n"] == 6]
    assert len(new_rows) == 1
    assert new_rows[0]["job_id"] == job_id


def test_five_timeout_cycles_would_have_tripped_the_old_raw_count_check(
    patch_db_client,
):
    """Sanity check on the OLD comparison this task replaced: proves the
    pre-existing bug is real, i.e. that this test suite would have caught
    it. ``next_attempt_n`` alone (the old cap-check source) returns 6 for
    the same seeded rows, which is > MAX_ATTEMPTS_PER_JOB (3) — the old
    code path. ``count_attempts_toward_cap`` (the fix) returns 0, staying
    under the cap.
    """
    import jobpipe.db as db
    from jobpipe.config import MAX_ATTEMPTS_PER_JOB

    job_id = "cap-timeout-job-2"
    seeded_rows = [
        {
            "id": i, "job_id": job_id, "attempt_n": i,
            "outcome": "in_progress", "notes": {"timeout": True},
        }
        for i in range(1, 6)
    ]
    fake_db = _FakeSupabaseClient(seeded_rows)
    patch_db_client(fake_db)

    old_check_value = db.next_attempt_n(job_id)
    assert old_check_value > MAX_ATTEMPTS_PER_JOB, (
        "expected the raw next_attempt_n counter to exceed the cap after "
        "5 timeout cycles — this is exactly the bug Task 5 fixes"
    )

    new_check_value = db.count_attempts_toward_cap(job_id)
    assert new_check_value < MAX_ATTEMPTS_PER_JOB, (
        "count_attempts_toward_cap must exclude timeout-marked rows so the "
        "job stays re-queueable"
    )
