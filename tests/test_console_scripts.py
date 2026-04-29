"""tests/test_console_scripts.py — pin the live console-script wiring.

PR-11 retired the Browserbase + Stagehand path; PR-13 then split the
remaining tailor cycle into two narrower entry points so an automated
trigger (CI, cron) can hit tailor (CI-friendly, LLM + LaTeX only)
without dragging in the visible-browser pre-fill phase that needs a
human at the keyboard.

Current wiring (verified by these tests):

    jobpipe-hunt   = jobpipe.hunt.agent:run
    jobpipe-tailor = jobpipe.tailor.pipeline:run_tailor_only
    jobpipe-submit = jobpipe.tailor.pipeline:run_submit_only

A typo / accidental delete in pyproject.toml is caught on the next
``pytest`` run. Critically, ``jobpipe-submit`` must NOT be wired back
to the retired ``jobpipe.submit.runner`` (now ``runner_legacy``) — that
binding pointed at the Browserbase + Stagehand runtime and was retired
in PR-11.
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


def test_jobpipe_hunt_console_script_present():
    scripts = _project_scripts()
    assert scripts.get("jobpipe-hunt") == "jobpipe.hunt.agent:run"


def test_jobpipe_tailor_points_at_run_tailor_only():
    """PR-13 split: jobpipe-tailor runs tailoring only, no pre-fill.

    Pre-PR-13 this pointed at ``jobpipe.tailor.pipeline:run`` which
    invoked the combined cycle. The combined entry point still exists
    (``run_cycle`` / ``run``) for tools that already invoke it
    directly, but the console-script wiring is now the narrower
    ``run_tailor_only``.
    """
    scripts = _project_scripts()
    assert (
        scripts.get("jobpipe-tailor")
        == "jobpipe.tailor.pipeline:run_tailor_only"
    )


def test_jobpipe_submit_points_at_run_submit_only():
    """PR-13 re-introduces jobpipe-submit pointing at the local-Playwright
    pre-fill path (``run_submit_only``), NOT the retired Browserbase
    runner.
    """
    scripts = _project_scripts()
    assert (
        scripts.get("jobpipe-submit")
        == "jobpipe.tailor.pipeline:run_submit_only"
    )


def test_jobpipe_submit_does_not_point_at_legacy_runner():
    """Belt-and-braces guard against accidental revert.

    The Path-B Browserbase + Stagehand runner was retired in PR-11; its
    code lives at ``jobpipe.submit.runner_legacy`` for forensic
    reference but has no console-script binding. PR-13 reused the
    ``jobpipe-submit`` script name on purpose for the new
    local-Playwright pre-fill path. A future revert that re-wires
    ``jobpipe-submit`` back to ``jobpipe.submit.runner:run`` (or
    ``runner_legacy:run``) would silently revive the retired runtime.
    Fail loudly here instead.
    """
    scripts = _project_scripts()
    submit_target = scripts.get("jobpipe-submit", "")
    assert "runner_legacy" not in submit_target, (
        f"jobpipe-submit must not point at the retired runner_legacy "
        f"module. Current value: {submit_target!r}"
    )
    assert submit_target != "jobpipe.submit.runner:run", (
        "jobpipe-submit must not point at jobpipe.submit.runner:run "
        "(the Path-B Browserbase runner retired in PR-11)."
    )
