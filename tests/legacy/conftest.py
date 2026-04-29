"""tests/legacy/conftest.py — Path-B forensic suite isolation.

Stamps every test collected under ``tests/legacy/`` with the
``legacy`` pytest marker so the default ``pytest`` invocation skips
them (see ``pyproject.toml::[tool.pytest.ini_options]::addopts``). The
suite under here exercises the retired Browserbase + Stagehand
adapters; we keep it runnable for forensics (``pytest -m legacy``) but
do NOT gate future changes on its health.

Why ``pytest_collection_modifyitems`` and not module-level
``pytestmark``: ``pytestmark`` in a conftest.py is not propagated to
sibling test modules — pytest applies that attribute only to tests
defined in the same module, and conftest.py defines none. The hook
below catches every item collected from this directory and tags it
with the marker, which is the canonical "directory-wide marker"
pattern.

This avoids touching the test file's body: the import sites inside
``test_submit_scaffold.py`` still resolve (none of the legacy modules
physically moved during this PR), so editing a 1000-line file we are
explicitly NOT maintaining is unnecessary.
"""

from __future__ import annotations

from pathlib import Path

import pytest

_LEGACY_DIR = Path(__file__).resolve().parent


def pytest_collection_modifyitems(config, items):
    """Apply ``@pytest.mark.legacy`` to every test collected from this dir."""
    for item in items:
        try:
            item_path = Path(str(item.fspath)).resolve()
        except Exception:
            continue
        if _LEGACY_DIR in item_path.parents or item_path == _LEGACY_DIR:
            item.add_marker(pytest.mark.legacy)
