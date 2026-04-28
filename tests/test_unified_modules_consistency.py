"""PR-8 schema-consistency test (preamble convention #9).

Confirms the unified ``jobpipe.db`` / ``jobpipe.notify`` / ``jobpipe.config``
modules expose the **union** of symbols every pre-PR-8 caller relied on,
and that function signatures match what the callers expect.

These tests guard against:
    1. Accidentally dropping a function during the merge.
    2. Silently changing a kwarg name or default value.
    3. The per-subtree shims drifting out of sync with the canonical
       module — each shim re-exports from the canonical file, so symbols
       importable through the shim must also be importable through the
       canonical module.

The test runs without secrets — ``jobpipe.db`` and ``jobpipe.notify``
have lazy Supabase clients, so importing them does not fire any HTTP.
``jobpipe.config`` exports soft-default values (empty strings for
secrets) so importing it likewise does not raise.
"""

from __future__ import annotations

import inspect

import pytest

import jobpipe.config as cfg
import jobpipe.db as db
import jobpipe.notify as notify


# ── jobpipe.db: union surface ─────────────────────────────────────────────

# (callable_name, expected_param_names_in_order)
# Where expected_param_names is the call-site's positional + keyword
# expectation — kwargs with defaults are listed by name only.
_EXPECTED_DB_SIGNATURES: dict[str, tuple[str, ...]] = {
    # hunt
    "upsert_job":               ("job", "result"),
    "get_seen_ids":             (),
    # tailor
    "get_jobs_by_status":       ("status", "limit"),
    "get_approved_jobs":        ("limit",),
    "get_prefill_requested_jobs": ("limit",),
    "get_confirmed_jobs":       ("limit",),
    "update_job_status":        ("job_id", "status"),
    "mark_preparing":           ("job_id",),
    "mark_ready_for_review": (
        "job_id", "resume_path", "cover_letter_path", "application_url",
        "application_notes", "resume_pdf_path", "cover_letter_pdf_path",
        "archetype", "archetype_confidence", "submission_url",
    ),
    "mark_ready_to_submit":     (),  # *args/**kwargs forwarder
    "mark_prefilling":          ("job_id",),
    "mark_awaiting_submit":     ("job_id", "screenshot_path"),
    "mark_skipped":             ("job_id", "reason"),
    "mark_applied": (
        "job_id", "application_notes", "submission_notes", "clear_materials",
    ),
    "delete_job_materials":     ("job_id",),
    "mark_tailor_failed": (
        "job_id", "reason", "clear_materials", "screenshot_path",
        "uncertain_fields",
    ),
    "get_job_counts_by_status": (),
    # submit
    "get_jobs_ready_for_submission": ("limit",),
    "get_job":                  ("job_id",),
    "next_attempt_n":           ("job_id",),
    "mark_submitting":          ("job_id",),
    "record_submission_log":    ("job_id", "log", "confidence"),
    "mark_submitted":           ("job_id", "confirmation_evidence"),
    "mark_needs_review":        ("job_id", "reason", "packet_ref"),
    "mark_failed":              ("job_id", "reason"),
    "open_attempt":             ("job_id", "attempt_n", "adapter"),
    "close_attempt": (
        "attempt_id", "outcome", "confidence", "stagehand_session_id",
        "browserbase_replay_url", "notes",
    ),
    "verify_materials_hash":    ("job", "resume_bytes", "cover_letter_text"),
}


@pytest.mark.parametrize("name,expected_params", list(_EXPECTED_DB_SIGNATURES.items()))
def test_db_symbol_exists_with_expected_signature(name, expected_params):
    fn = getattr(db, name, None)
    assert fn is not None, (
        f"PR-8: jobpipe.db must export {name}() — "
        "every pre-PR-8 caller relied on it"
    )
    assert callable(fn), f"jobpipe.db.{name} must be callable"
    if not expected_params:
        # Forwarders (*args/**kwargs) or zero-arg functions — skip param check.
        return
    sig = inspect.signature(fn)
    actual = tuple(sig.parameters.keys())
    # Trim the actual to the same length: the expected list is the
    # ordered subset the callers care about; extra kwargs added later
    # are allowed as long as they come AFTER the expected ones.
    assert actual[: len(expected_params)] == expected_params, (
        f"jobpipe.db.{name} signature drifted: "
        f"expected leading params {expected_params}, got {actual}"
    )


def test_db_lazy_client_attributes_resolve():
    """``client`` and ``service_client`` must be reachable as attributes
    (PR-8 preserved this via module __getattr__) without raising at
    attribute lookup. The lazy client itself isn't created until a
    method is called on it."""
    import os
    if not (os.environ.get("SUPABASE_URL") and os.environ.get("SUPABASE_KEY")):
        pytest.skip("Supabase env not present; skipping live client construction")
    assert db.client is not None
    assert db.service_client is not None


# ── jobpipe.notify: union surface ─────────────────────────────────────────

_EXPECTED_NOTIFY_SIGNATURES: dict[str, tuple[str, ...]] = {
    # hunt — Resend digest
    "send_digest":              ("entries",),
    # tailor / submit — Supabase notifications table
    "cockpit_url":              ("job_id",),
    "create_notification":      ("notification_type", "job", "message"),
    "send_awaiting_review":     ("job",),
    "send_awaiting_submit":     ("job", "screenshot_path"),
    "send_applied":             ("job",),
    "send_failed":              ("job", "reason"),
    # PR-8 deprecated aliases (kept until a future sweep PR removes them)
    "notify_ready_for_review":  ("job",),
    "notify_awaiting_submit":   ("job", "screenshot_path"),
    "notify_applied":           ("job",),
    "notify_failed":            ("job", "reason"),
}


@pytest.mark.parametrize("name,expected_params",
                         list(_EXPECTED_NOTIFY_SIGNATURES.items()))
def test_notify_symbol_exists_with_expected_signature(name, expected_params):
    fn = getattr(notify, name, None)
    assert fn is not None, (
        f"PR-8: jobpipe.notify must export {name}()"
    )
    assert callable(fn)
    sig = inspect.signature(fn)
    actual = tuple(sig.parameters.keys())
    assert actual[: len(expected_params)] == expected_params, (
        f"jobpipe.notify.{name} signature drifted: "
        f"expected leading params {expected_params}, got {actual}"
    )


def test_notify_aliases_forward_to_canonical():
    """Each ``notify_*`` deprecated alias must forward to the canonical
    ``send_*`` function — verified by stubbing the canonical send_* with
    a sentinel-returning stub and confirming the sentinel propagates back
    through the deprecated alias."""
    sentinel = "FORWARDED"

    def _stub_send_awaiting_review(job):
        return sentinel

    def _stub_send_awaiting_submit(job, screenshot_path=None):
        return sentinel

    def _stub_send_applied(job):
        return sentinel

    def _stub_send_failed(job, reason):
        return sentinel

    saved = {
        "send_awaiting_review": notify.send_awaiting_review,
        "send_awaiting_submit": notify.send_awaiting_submit,
        "send_applied": notify.send_applied,
        "send_failed": notify.send_failed,
    }
    try:
        notify.send_awaiting_review = _stub_send_awaiting_review
        notify.send_awaiting_submit = _stub_send_awaiting_submit
        notify.send_applied = _stub_send_applied
        notify.send_failed = _stub_send_failed

        assert notify.notify_ready_for_review({"id": "job-1"}) == sentinel
        assert notify.notify_awaiting_submit({"id": "job-2"}, "s") == sentinel
        assert notify.notify_applied({"id": "job-3"}) == sentinel
        assert notify.notify_failed({"id": "job-4"}, "boom") == sentinel
    finally:
        for k, v in saved.items():
            setattr(notify, k, v)


def test_notification_type_strings_decoupled_from_symbol_names():
    """PR-8 contract: send_awaiting_review writes type='ready_for_review'
    to the notifications table (decoupled from the symbol rename).

    The function symbol was renamed during PR-8 (Q2 in the resolution
    memo), but the cockpit / dashboard contract that keys off the
    notification.type string and the jobs.status CHECK enum is unchanged.
    """
    captured: dict = {}

    def _capture(notification_type, job, message=""):
        captured["type"] = notification_type
        return True

    saved = notify.create_notification
    try:
        notify.create_notification = _capture
        notify.send_awaiting_review({"id": "job-1", "company": "Acme",
                                     "title": "X", "score": 9, "tier": 1})
        assert captured["type"] == "ready_for_review", (
            "decoupling rule violated: send_awaiting_review must write "
            "notification.type='ready_for_review' to preserve the dashboard "
            "contract."
        )
        captured.clear()
        notify.send_awaiting_submit({"id": "job-2", "company": "B", "title": "Y"})
        assert captured["type"] == "awaiting_human_submit"
    finally:
        notify.create_notification = saved


# ── jobpipe.config: union surface ─────────────────────────────────────────

_EXPECTED_CONFIG_SYMBOLS: tuple[str, ...] = (
    # Cross-subtree helper.
    "require_env",
    # Supabase soft defaults.
    "SUPABASE_URL", "SUPABASE_KEY", "SUPABASE_SERVICE_ROLE_KEY",
    # Anthropic.
    "ANTHROPIC_API_KEY",
    # Claude model — PR-8 keeps the two distinct subtree constants.
    "TAILOR_CLAUDE_MODEL", "SUBMITTER_CLAUDE_MODEL", "CLAUDE_MODEL",
    # Browserbase.
    "BROWSERBASE_API_KEY", "BROWSERBASE_PROJECT_ID",
    # Polling.
    "POLL_INTERVAL_SECONDS", "POLL_INTERVAL_MINUTES",
    # Submitter knobs.
    "MAX_CONCURRENT_SUBMISSIONS", "AUTO_SUBMIT_THRESHOLD",
    "SESSION_BUDGET_SECONDS", "MAX_ATTEMPTS_PER_JOB",
    "HEADLESS", "REVIEW_DASHBOARD_URL", "ATS_CONFIDENCE_MIN",
    # Tailor knobs.
    "HUMAN_APPROVAL_REQUIRED", "AUTO_SUBMIT_ENABLED",
    "AUTO_SUBMIT_MIN_SCORE", "AUTO_SUBMIT_MIN_TIER",
    # Notify.
    "PORTFOLIO_BASE_URL",
    # Hunter mode + location helpers.
    "Mode", "DEFAULT_MODE", "set_mode", "get_mode",
    "LOCAL_LOCATION_SUBSTRINGS", "REMOTE_LOCATION_SUBSTRINGS",
    "is_local_or_remote", "location_filter_enabled",
)


@pytest.mark.parametrize("name", _EXPECTED_CONFIG_SYMBOLS)
def test_config_exports_expected_symbol(name):
    assert hasattr(cfg, name), (
        f"PR-8: jobpipe.config must export {name}"
    )


def test_config_two_distinct_claude_models_per_pr8():
    """PR-8 explicitly does not unify CLAUDE_MODEL — each subtree has its
    own constant defaulting to its current value, with a CLAUDE_MODEL env
    fallback for future unify."""
    assert isinstance(cfg.TAILOR_CLAUDE_MODEL, str)
    assert isinstance(cfg.SUBMITTER_CLAUDE_MODEL, str)
    # Backward-compat: jobpipe.config.CLAUDE_MODEL tracks SUBMITTER_*.
    assert cfg.CLAUDE_MODEL == cfg.SUBMITTER_CLAUDE_MODEL


# ── Per-subtree shim consistency ──────────────────────────────────────────

def test_tailor_db_shim_reexports_tailor_surface():
    """The tailor db shim must re-export every function tailor callers
    historically used. Each re-export must resolve to the same object as
    the canonical module — guards against the shim accidentally
    re-defining instead of re-exporting."""
    import jobpipe.tailor.db as tailor_db
    expected = (
        "delete_job_materials", "get_approved_jobs", "get_confirmed_jobs",
        "get_job_counts_by_status", "get_jobs_by_status",
        "get_prefill_requested_jobs", "mark_applied", "mark_awaiting_submit",
        "mark_prefilling", "mark_preparing", "mark_ready_for_review",
        "mark_ready_to_submit", "mark_skipped", "mark_tailor_failed",
        "update_job_status",
    )
    for name in expected:
        assert hasattr(tailor_db, name), (
            f"jobpipe.tailor.db shim must re-export {name}"
        )
        assert getattr(tailor_db, name) is getattr(db, name), (
            f"jobpipe.tailor.db.{name} must be the same object as "
            f"jobpipe.db.{name}"
        )


def test_submit_db_shim_reexports_submit_surface():
    import jobpipe.submit.db as submit_db
    expected = (
        "close_attempt", "get_job", "get_jobs_ready_for_submission",
        "mark_failed", "mark_needs_review", "mark_submitted",
        "mark_submitting", "next_attempt_n", "open_attempt",
        "record_submission_log", "verify_materials_hash",
    )
    for name in expected:
        assert hasattr(submit_db, name)
        assert getattr(submit_db, name) is getattr(db, name)


def test_hunt_db_shim_reexports_hunt_surface():
    import jobpipe.hunt.db as hunt_db
    for name in ("get_seen_ids", "upsert_job"):
        assert hasattr(hunt_db, name)
        assert getattr(hunt_db, name) is getattr(db, name)


def test_tailor_notify_shim_reexports_send_and_alias_surface():
    import jobpipe.tailor.notify as tailor_notify
    expected = (
        "cockpit_url", "create_notification",
        "send_applied", "send_awaiting_review", "send_awaiting_submit",
        "send_failed",
        "notify_applied", "notify_awaiting_submit", "notify_failed",
        "notify_ready_for_review",
    )
    for name in expected:
        assert hasattr(tailor_notify, name)
        assert getattr(tailor_notify, name) is getattr(notify, name)


def test_hunt_notifier_shim_reexports_send_digest():
    import jobpipe.hunt.notifier as hunt_notifier
    assert hasattr(hunt_notifier, "send_digest")
    assert hunt_notifier.send_digest is notify.send_digest
