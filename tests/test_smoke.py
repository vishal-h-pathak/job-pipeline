"""PR-0 smoke test — verifies the unified-repo skeleton imported correctly.

This file exists so `pytest` exits 0 (not 5: no-tests-collected) before any
production tests have migrated. It is replaced by real fixtures and tests
as packages migrate in PR-1..PR-10.
"""
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent


def test_subpackages_present() -> None:
    for sub in ("hunt", "tailor", "submit"):
        assert (REPO_ROOT / "jobpipe" / sub).is_dir(), f"jobpipe/{sub}/ missing"


def test_pyproject_present() -> None:
    assert (REPO_ROOT / "pyproject.toml").is_file()
