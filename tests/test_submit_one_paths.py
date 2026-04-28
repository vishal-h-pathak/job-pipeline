"""PR-6 smoke test for jobpipe/tailor/scripts/submit_one.py.

The script previously imported the deleted `mark_needs_review` alias.
PR-6 patched the imports + call sites to use `mark_tailor_failed`. This
test loads the script as a fresh module with stubbed dependencies,
fakes a UniversalApplicant result, and runs `main()` end-to-end to
prove:

  - the new import line resolves (no ImportError on `mark_needs_review`)
  - each of the three failure paths invokes mark_tailor_failed with
    `clear_materials=False` (so the human can re-run the script with
    the existing PDFs in Storage)
  - the success path invokes mark_applied (untouched by PR-6)

Stays static — no Browserbase, no Anthropic, no Supabase.
"""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock


REPO_ROOT = Path(__file__).resolve().parent.parent
SCRIPT_PATH = (
    REPO_ROOT / "jobpipe" / "tailor" / "scripts" / "submit_one.py"
)


def _load_script(monkeypatch, fake_db, fake_applicant):
    """Load submit_one.py as a fresh module with stubbed deps.

    The script does ``from db import ...`` and instantiates
    ``UniversalApplicant`` from ``applicant.universal``. Both are
    pre-installed in ``sys.modules`` so the actual top-level imports
    in submit_one.py resolve to our stubs.
    """
    tailor_dir = REPO_ROOT / "jobpipe" / "tailor"
    monkeypatch.syspath_prepend(str(tailor_dir))

    monkeypatch.setitem(sys.modules, "db", fake_db)

    applicant_pkg = type(sys)("applicant")
    universal_mod = type(sys)("applicant.universal")
    universal_mod.UniversalApplicant = lambda *a, **kw: fake_applicant
    applicant_pkg.universal = universal_mod
    monkeypatch.setitem(sys.modules, "applicant", applicant_pkg)
    monkeypatch.setitem(sys.modules, "applicant.universal", universal_mod)

    # The script calls dotenv.load_dotenv at top level — stub to no-op.
    dotenv_mod = type(sys)("dotenv")
    dotenv_mod.load_dotenv = lambda *a, **kw: None
    monkeypatch.setitem(sys.modules, "dotenv", dotenv_mod)

    spec = importlib.util.spec_from_file_location(
        "_pr6_submit_one_under_test", SCRIPT_PATH
    )
    mod = importlib.util.module_from_spec(spec)
    monkeypatch.setitem(sys.modules, "_pr6_submit_one_under_test", mod)
    spec.loader.exec_module(mod)
    return mod


class _FakeSupabaseChain:
    """Minimal supabase-py-shaped chain that yields a single fixture job row."""
    def __init__(self, job_row: dict):
        self._row = job_row

    def table(self, _name): return self
    def select(self, _cols): return self
    def eq(self, _col, _val): return self
    def execute(self):
        return SimpleNamespace(data=[self._row])


def _make_fake_db(job_row: dict | None = None) -> SimpleNamespace:
    """db stub that ONLY exposes the symbols PR-6 left behind.

    If submit_one.py accidentally still references a deleted alias
    (mark_failed / mark_needs_review on the tailor side) we'd see
    AttributeError when the import is executed.
    """
    row = job_row or {
        "id": "stub-job",
        "url": "https://boards.greenhouse.io/x/jobs/1",
        "application_url": "https://boards.greenhouse.io/x/jobs/1",
        "company": "X",
        "title": "Y",
        "description": "job description",
        "resume_path": None,
        "cover_letter_path": None,
        "resume_pdf_path": None,
    }
    return SimpleNamespace(
        mark_applied=MagicMock(),
        mark_tailor_failed=MagicMock(),
        client=_FakeSupabaseChain(row),
    )


def _argv(job_id: str, mode: str, tmp_path: Path) -> list[str]:
    resume = tmp_path / "r.pdf"
    resume.write_bytes(b"%PDF-stub")
    cover = tmp_path / "c.txt"
    cover.write_text("cover body")
    jd = tmp_path / "jd.txt"
    jd.write_text("job description")
    return [
        "submit_one",
        "--job-id", job_id,
        "--mode", mode,
        "--url", f"https://boards.greenhouse.io/x/jobs/{job_id}",
        "--company", "X",
        "--title", "Y",
        "--resume", str(resume),
        "--cover-letter", str(cover),
        "--job-description", str(jd),
    ]


def test_prepare_mode_needs_review_routes_to_tailor_failed(monkeypatch, tmp_path):
    fake_db = _make_fake_db()
    fake_applicant = SimpleNamespace(
        apply=lambda *a, **kw: {
            "needs_review": True,
            "review_reason": "agent paused",
            "screenshots": ["job-materials/J/last.png"],
            "uncertain_fields": ["years_experience"],
        },
        submit=lambda *a, **kw: {},
    )
    mod = _load_script(monkeypatch, fake_db, fake_applicant)

    monkeypatch.setattr(sys, "argv", _argv("job-aaa", "prepare", tmp_path))
    mod.main()

    fake_db.mark_tailor_failed.assert_called_once()
    args, kwargs = fake_db.mark_tailor_failed.call_args
    assert args == ("job-aaa",)
    assert kwargs["reason"] == "agent paused"
    assert kwargs["clear_materials"] is False
    assert kwargs["screenshot_path"] == "job-materials/J/last.png"
    assert kwargs["uncertain_fields"] == ["years_experience"]
    fake_db.mark_applied.assert_not_called()


def test_submit_mode_submitted_routes_to_mark_applied(monkeypatch, tmp_path):
    fake_db = _make_fake_db()
    fake_applicant = SimpleNamespace(
        apply=lambda *a, **kw: {},
        submit=lambda *a, **kw: {
            "submitted": True,
            "submit_confirmation_text": "Application received",
        },
    )
    mod = _load_script(monkeypatch, fake_db, fake_applicant)

    monkeypatch.setattr(sys, "argv", _argv("job-bbb", "submit", tmp_path))
    mod.main()

    fake_db.mark_applied.assert_called_once_with(
        "job-bbb", application_notes="Application received"
    )
    fake_db.mark_tailor_failed.assert_not_called()


def test_submit_mode_no_outcome_routes_to_tailor_failed(monkeypatch, tmp_path):
    fake_db = _make_fake_db()
    fake_applicant = SimpleNamespace(
        apply=lambda *a, **kw: {},
        submit=lambda *a, **kw: {},  # neither submitted nor needs_review
    )
    mod = _load_script(monkeypatch, fake_db, fake_applicant)

    monkeypatch.setattr(sys, "argv", _argv("job-ccc", "submit", tmp_path))
    mod.main()

    fake_db.mark_tailor_failed.assert_called_once_with(
        "job-ccc",
        reason="submit did not complete",
        clear_materials=False,
    )
    fake_db.mark_applied.assert_not_called()


def test_script_does_not_import_deleted_alias(monkeypatch, tmp_path):
    """Loading the script must NOT trigger an ImportError for the deleted
    mark_needs_review (or fall back to mark_failed)."""
    fake_db = _make_fake_db()
    fake_applicant = SimpleNamespace(
        apply=lambda *a, **kw: {}, submit=lambda *a, **kw: {}
    )
    _load_script(monkeypatch, fake_db, fake_applicant)
