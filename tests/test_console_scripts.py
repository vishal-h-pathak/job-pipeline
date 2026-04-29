"""tests/test_console_scripts.py — guard against re-introducing ``jobpipe-submit``.

The local-Playwright consolidation retired the Browserbase + Stagehand
runner that ``jobpipe-submit`` pointed at. ``jobpipe.submit.runner`` was
renamed to ``runner_legacy.py`` and the console-script binding was
dropped from ``pyproject.toml::[project.scripts]``. This test
belt-and-braces against a future revert: if anyone re-adds
``jobpipe-submit``, this fails loudly with a pointer to the canonical
Path-A entry point.

Also pins the live console scripts (``jobpipe-hunt``, ``jobpipe-tailor``)
so a typo / accidental delete in pyproject.toml is caught on the next
``pytest`` run.
"""

from __future__ import annotations

from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parent.parent
PYPROJECT = REPO_ROOT / "pyproject.toml"


def _load_pyproject() -> dict:
    """Parse pyproject.toml using stdlib tomllib (3.11+) or tomli fallback."""
    try:
        import tomllib  # type: ignore[import]
    except ModuleNotFoundError:
        import tomli as tomllib  # type: ignore[import]
    with PYPROJECT.open("rb") as f:
        return tomllib.load(f)


def _project_scripts() -> dict:
    data = _load_pyproject()
    return data.get("project", {}).get("scripts", {})


def test_jobpipe_submit_console_script_is_absent():
    """The retired Path-B entry point must not come back via revert."""
    scripts = _project_scripts()
    assert "jobpipe-submit" not in scripts, (
        "jobpipe-submit was retired during the local-Playwright "
        "consolidation. The canonical pre-fill path is now part of "
        "`jobpipe-tailor` (jobpipe.tailor.pipeline:run, which calls "
        "process_prefill_requested_jobs). Do not re-add this script "
        "without first reviving Path B."
    )


def test_jobpipe_hunt_console_script_present():
    scripts = _project_scripts()
    assert scripts.get("jobpipe-hunt") == "jobpipe.hunt.agent:run"


def test_jobpipe_tailor_console_script_present():
    scripts = _project_scripts()
    assert scripts.get("jobpipe-tailor") == "jobpipe.tailor.pipeline:run"
