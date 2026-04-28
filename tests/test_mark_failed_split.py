"""PR-6 consistency tests for the mark_failed / mark_tailor_failed split.

Two angles, both static or stubbed — no Supabase or Storage round-trip:

1. Source-level invariants (AST + text scan):
   - Tailor side defines `mark_tailor_failed`; does NOT define
     `mark_failed` or `mark_needs_review` at module top level.
   - Submit side keeps both `mark_failed` and `mark_needs_review`.
   - No file under `jobpipe/tailor/` references `mark_needs_review`
     (the M-2 deprecated alias was deleted).
   - No file under `jobpipe/tailor/` references `mark_failed` from `db`
     (the rename means callers must use `mark_tailor_failed`).

2. Behavior shape (monkeypatched supabase client):
   - `mark_tailor_failed` writes status='failed' + failure_reason and,
     when clear_materials=True (default), nulls resume_pdf_path and
     cover_letter_pdf_path.
   - `mark_tailor_failed(..., clear_materials=False)` does NOT touch
     the PDF path columns and does NOT call delete_all_for_job.
   - `mark_tailor_failed(..., screenshot_path=..., uncertain_fields=...)`
     persists those debug fields (the previous mark_needs_review alias
     used to write these; mark_tailor_failed inherits the capability).
   - Submit's `mark_failed` writes status='failed' + failure_reason +
     status_updated_at — does NOT clear materials. Attempts-row
     bookkeeping is the runner's responsibility.
   - Submit's `mark_needs_review` writes status='needs_review' (the
     real status, distinct from the deleted tailor alias).
"""

from __future__ import annotations

import ast
import sys
from pathlib import Path

import pytest


REPO_ROOT = Path(__file__).resolve().parent.parent
TAILOR_DIR = REPO_ROOT / "jobpipe" / "tailor"
SUBMIT_DIR = REPO_ROOT / "jobpipe" / "submit"
# PR-8: per-subtree db.py files are now shims that re-export from the
# canonical jobpipe/db.py. AST-level invariants check the canonical file
# directly; runtime fixtures still resolve through the shims unchanged.
CANONICAL_DB = REPO_ROOT / "jobpipe" / "db.py"


def _toplevel_funcs(py_path: Path) -> set[str]:
    tree = ast.parse(py_path.read_text(encoding="utf-8"))
    return {
        node.name
        for node in tree.body
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
    }


# ── Source-level invariants ───────────────────────────────────────────────


def test_canonical_db_defines_mark_tailor_failed():
    """Canonical jobpipe/db.py defines mark_tailor_failed (PR-6 split)."""
    funcs = _toplevel_funcs(CANONICAL_DB)
    assert "mark_tailor_failed" in funcs, (
        "PR-6/PR-8: jobpipe/db.py must define mark_tailor_failed"
    )


def test_canonical_db_keeps_mark_failed_and_mark_needs_review():
    """Canonical jobpipe/db.py defines both submit-side state transitions."""
    funcs = _toplevel_funcs(CANONICAL_DB)
    assert "mark_failed" in funcs
    assert "mark_needs_review" in funcs


def _ast_referenced_names(py_path: Path) -> set[str]:
    """Return all Name / attribute / import-alias identifiers used in code.

    Skips bare strings, docstrings, and comments — lets prose mention
    the deleted symbols freely while catching real call sites.
    """
    tree = ast.parse(py_path.read_text(encoding="utf-8"))
    names: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Name):
            names.add(node.id)
        elif isinstance(node, ast.Attribute):
            names.add(node.attr)
        elif isinstance(node, ast.alias):
            # `from db import mark_failed` -> 'mark_failed'
            names.add(node.asname or node.name.split(".")[-1])
    return names


def test_no_tailor_caller_references_mark_needs_review():
    """No tailor module's CODE (not comments) calls or imports mark_needs_review."""
    hits = []
    for py in TAILOR_DIR.rglob("*.py"):
        if "mark_needs_review" in _ast_referenced_names(py):
            hits.append(str(py.relative_to(REPO_ROOT)))
    assert not hits, (
        "PR-6: mark_needs_review must not be called/imported in jobpipe/tailor/ "
        "(deprecated alias was removed). Files: " + ", ".join(hits)
    )


def test_no_tailor_caller_references_mark_failed():
    """No tailor module's CODE calls or imports `mark_failed` (use mark_tailor_failed)."""
    bad = []
    for py in TAILOR_DIR.rglob("*.py"):
        if "mark_failed" in _ast_referenced_names(py):
            bad.append(str(py.relative_to(REPO_ROOT)))
    assert not bad, (
        "PR-6: tailor callers must use mark_tailor_failed, not mark_failed. "
        "Files: " + ", ".join(bad)
    )


# ── Behavior shape (stubbed supabase client) ──────────────────────────────


class _FakeUpdateChain:
    """Records the latest .update(...).eq(...).execute() chain on a fake table."""
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
def tailor_db(monkeypatch):
    """Import jobpipe/tailor/db.py with a stubbed supabase client + config.

    Tailor's db.py uses the bare-import pattern (`from config import ...`)
    so we install a synthetic `config` module and patch
    `supabase.create_client` before triggering the import.
    """
    monkeypatch.syspath_prepend(str(TAILOR_DIR))

    config_stub = type(sys)("config")
    config_stub.SUPABASE_URL = "https://example.supabase.co"
    config_stub.SUPABASE_KEY = "anon-test"
    monkeypatch.setitem(sys.modules, "config", config_stub)

    fake = _FakeClient()
    import supabase  # type: ignore[import-not-found]
    monkeypatch.setattr(supabase, "create_client", lambda *a, **kw: fake)

    # PR-8: tailor/db.py is now a shim that re-exports from the canonical
    # jobpipe.db, which holds a lazy module-level Supabase client cache.
    # Reset it (per-test) so the create_client monkeypatch above is what
    # builds the next client, not whatever fixture ran earlier.
    import jobpipe.db as _canonical_db
    monkeypatch.setattr(_canonical_db, "_client", None)
    monkeypatch.setattr(_canonical_db, "_service_client", None)

    # `mark_tailor_failed(clear_materials=True)` does `from storage import
    # delete_all_for_job` lazily — provide a stub so we can observe calls.
    storage_stub = type(sys)("storage")
    delete_calls: list[str] = []
    storage_stub.delete_all_for_job = lambda jid: delete_calls.append(jid)
    monkeypatch.setitem(sys.modules, "storage", storage_stub)

    sys.modules.pop("db", None)
    import db  # noqa: E402
    db.client = fake  # safety net if module-level client was created earlier
    yield db, fake, delete_calls
    sys.modules.pop("db", None)


def test_mark_tailor_failed_default_clears_materials(tailor_db):
    db, fake, delete_calls = tailor_db
    db.mark_tailor_failed("job-123", "LaTeX compile error")

    payload = fake.calls["update_payload"]
    assert payload["status"] == "failed"
    assert payload["failure_reason"] == "LaTeX compile error"
    assert "status_updated_at" in payload
    assert payload["resume_pdf_path"] is None
    assert payload["cover_letter_pdf_path"] is None
    assert delete_calls == ["job-123"]


def test_mark_tailor_failed_no_clear_keeps_materials(tailor_db):
    db, fake, delete_calls = tailor_db
    db.mark_tailor_failed(
        "job-456", "Pre-fill: page load failed", clear_materials=False,
    )

    payload = fake.calls["update_payload"]
    assert payload["status"] == "failed"
    assert payload["failure_reason"] == "Pre-fill: page load failed"
    assert "resume_pdf_path" not in payload
    assert "cover_letter_pdf_path" not in payload
    assert delete_calls == [], "clear_materials=False must not delete from Storage"


def test_mark_tailor_failed_persists_screenshot_and_uncertain_fields(tailor_db):
    db, fake, _ = tailor_db
    db.mark_tailor_failed(
        "job-789", "agent paused",
        clear_materials=False,
        screenshot_path="job-materials/job-789/review.png",
        uncertain_fields=["years_experience"],
    )
    payload = fake.calls["update_payload"]
    assert payload["review_screenshot"] == "job-materials/job-789/review.png"
    assert payload["uncertain_fields"] == ["years_experience"]


@pytest.fixture
def submit_db(monkeypatch):
    """Import jobpipe/submit/db.py with a stubbed supabase client + env."""
    monkeypatch.syspath_prepend(str(SUBMIT_DIR))
    for k, v in {
        "SUPABASE_URL": "https://example.supabase.co",
        "SUPABASE_KEY": "anon-test",
        "SUPABASE_SERVICE_ROLE_KEY": "service-test",
        "BROWSERBASE_API_KEY": "bb-test",
        "BROWSERBASE_PROJECT_ID": "bb-proj-test",
        "ANTHROPIC_API_KEY": "sk-test",
    }.items():
        monkeypatch.setenv(k, v)

    fake = _FakeClient()
    import supabase  # type: ignore[import-not-found]
    monkeypatch.setattr(supabase, "create_client", lambda *a, **kw: fake)

    # PR-8: submit/db.py is now a shim that re-exports from the canonical
    # jobpipe.db. Reset its lazy client cache so the create_client
    # monkeypatch above is what builds the next client.
    import jobpipe.db as _canonical_db
    monkeypatch.setattr(_canonical_db, "_client", None)
    monkeypatch.setattr(_canonical_db, "_service_client", None)

    for m in ("config", "db"):
        sys.modules.pop(m, None)
    import db  # noqa: E402
    db.client = fake
    db.service_client = fake
    yield db, fake
    for m in ("config", "db"):
        sys.modules.pop(m, None)


def test_submit_mark_failed_writes_status_and_reason_only(submit_db):
    db, fake = submit_db
    db.mark_failed("job-abc", "adapter aborted")
    payload = fake.calls["update_payload"]
    assert payload["status"] == "failed"
    assert payload["failure_reason"] == "adapter aborted"
    assert "status_updated_at" in payload
    # Submit's mark_failed must NOT clear materials — the cockpit may
    # want them attached for re-attempt or human review.
    assert "resume_pdf_path" not in payload
    assert "cover_letter_pdf_path" not in payload


def test_submit_mark_needs_review_writes_real_needs_review_status(submit_db):
    db, fake = submit_db
    db.mark_needs_review("job-def", "low confidence", packet_ref="packets/job-def.json")
    payload = fake.calls["update_payload"]
    assert payload["status"] == "needs_review"
    assert payload["failure_reason"] == "low confidence"
    assert payload["review_packet"] == "packets/job-def.json"
