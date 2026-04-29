#!/usr/bin/env python3
"""scripts/smoke.py — end-to-end pipeline smoke test.

Exercises the import surface and core wiring of hunt → tailor → submit
without hitting Supabase, Browserbase, or Anthropic. Designed for
`make smoke` so CI can run unit tests and pipeline-imports in parallel.

Stage markers are printed to stdout (`✓ stage: <name>`); the first
failure prints `✗ stage: <name> — <repr>` to stderr and exits 1.
Wall-clock budget: 60 s.

Stages:
    0. profile.load_profile() round-trips fixture profile.yml
    1. jobpipe.hunt.agent + jobpipe.hunt.scorer import; entry point reachable
    2. jobpipe.tailor.pipeline imports; entry point reachable
    3. jobpipe.submit.runner imports; router resolves greenhouse adapter
    4. hunt → tailor → submit data flow connects via beacon_job.json
       and a fake_browser surface (mirrors tests/conftest.py fixtures)
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
import time
import traceback
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
FIXTURES = ROOT / "tests" / "fixtures"


def stage(name: str) -> None:
    print(f"✓ stage: {name}", flush=True)


def fail(name: str, exc: BaseException | str) -> None:
    msg = exc if isinstance(exc, str) else repr(exc)
    print(f"✗ stage: {name} — {msg}", file=sys.stderr, flush=True)
    if isinstance(exc, BaseException):
        traceback.print_exc()
    sys.exit(1)


# Stub env so jobpipe.config / submit/config don't blow up at import.
# These propagate to subprocesses below via os.environ.
os.environ.setdefault("SUPABASE_URL", "https://example.invalid")
os.environ.setdefault("SUPABASE_KEY", "smoke-anon")
os.environ.setdefault("SUPABASE_SERVICE_ROLE_KEY", "smoke-service")
os.environ.setdefault("BROWSERBASE_API_KEY", "smoke-bb")
os.environ.setdefault("BROWSERBASE_PROJECT_ID", "smoke-bb-proj")
os.environ.setdefault("ANTHROPIC_API_KEY", "smoke-anthropic")
os.environ["JOBPIPE_PROFILE_DIR"] = str(FIXTURES / "profile")


def _subprocess_check(name: str, code: str, timeout: float = 30.0) -> None:
    """Run `code` in a fresh Python interpreter; exit on non-zero.

    Each subtree's `from prompts import ...` and `from storage import ...`
    bare imports resolve against whichever `jobpipe/<subtree>/` got onto
    sys.path *first*; once Python caches a module like `prompts` in
    `sys.modules`, a second subtree trying to import its own `prompts`
    package returns the cached one. This is a pre-existing design choice
    of the subtree-merged repo — production never hits it because each
    console script (`jobpipe-hunt`, `jobpipe-tailor`, `jobpipe-submit`)
    runs in its own process. The smoke harness mirrors that by running
    each subtree-import check in its own subprocess.
    """
    try:
        result = subprocess.run(
            [sys.executable, "-c", code],
            cwd=str(ROOT),
            capture_output=True,
            text=True,
            timeout=timeout,
            env=os.environ.copy(),
        )
    except subprocess.TimeoutExpired:
        fail(name, f"subprocess timed out after {timeout}s")
        return
    if result.returncode != 0:
        sys.stderr.write(result.stderr)
        fail(name, f"subprocess exited {result.returncode}")


t0 = time.time()


# 0: profile.load_profile() validates fixture profile.yml
try:
    from jobpipe import profile_loader

    profile_loader._clear_cache_for_tests()
    p = profile_loader.load_profile()
    if not isinstance(p, dict):
        raise AssertionError(f"load_profile() returned {type(p).__name__}, expected dict")
    if not p.get("identity"):
        raise AssertionError("profile.yml missing 'identity' section")
    if not p.get("application_defaults"):
        raise AssertionError("profile.yml missing 'application_defaults' section")
    defaults = p["application_defaults"]
    if defaults.get("work_authorization") != "us_citizen":
        raise AssertionError(
            f"application_defaults.work_authorization={defaults.get('work_authorization')!r}, "
            "expected 'us_citizen' — fixture profile.yml drift"
        )
    stage("profile.load_profile() round-trips fixture profile.yml")
except Exception as e:
    fail("profile.validate", e)


# 1: hunt imports + scorer + agent reachable (subprocess: clean module cache)
_subprocess_check(
    "hunt.import",
    """
import sys
from jobpipe.hunt import agent as hunt_agent
from jobpipe.hunt import scorer as hunt_scorer
assert callable(getattr(hunt_agent, "run", None)), "hunt.agent.run not callable"
assert callable(getattr(hunt_scorer, "score_job", None)), "hunt.scorer.score_job not callable"
""",
)
stage("hunt.agent + hunt.scorer imported (jobpipe-hunt entry point reachable)")


# 2: tailor imports + pipeline + run_cycle reachable (subprocess)
_subprocess_check(
    "tailor.import",
    """
import sys
from jobpipe.tailor import pipeline as tailor_pipeline
assert callable(getattr(tailor_pipeline, "run", None)), "tailor.pipeline.run not callable"
assert callable(getattr(tailor_pipeline, "run_cycle", None)), "tailor.pipeline.run_cycle not callable"
""",
)
stage("tailor.pipeline imported (jobpipe-tailor entry point reachable)")


# 3: submit imports + router resolves greenhouse adapter (subprocess)
_subprocess_check(
    "submit.import",
    """
from jobpipe.submit import runner as submit_runner  # also triggers sys.path bootstrap
assert callable(getattr(submit_runner, "run", None)), "submit.runner.run not callable"
import router
router._REGISTRY.clear()
adapter = router.get_adapter("greenhouse")
assert adapter.name == "greenhouse", f"router resolved {adapter.name!r}, expected 'greenhouse'"
""",
)
stage("submit.runner + router.get_adapter('greenhouse') resolves")


# 4: hunt -> tailor -> submit data flow with fake_browser (in-process; no
#    `prompts` collision risk — only ats_detect + adapters.base touched).
try:
    job = json.loads((FIXTURES / "beacon_job.json").read_text(encoding="utf-8"))

    from jobpipe.shared.ats_detect import detect_ats

    detected = detect_ats(job["application_url"])
    if detected != "greenhouse":
        raise AssertionError(f"detect_ats({job['application_url']!r}) = {detected!r}, expected 'greenhouse'")
    if job["ats_kind"] != detected:
        raise AssertionError(
            f"beacon_job.ats_kind={job['ats_kind']!r} disagrees with detect_ats result {detected!r}"
        )

    # Build a SubmissionContext with the fake-browser surface, mirroring
    # the tests/conftest.py fixtures so the smoke and the unit tests
    # exercise the same shape. Importing `jobpipe.submit.runner` here
    # triggers its sys.path bootstrap so the bare `from adapters.base`
    # resolves; subprocess stage 3 only verified import in isolation.
    import jobpipe.submit.runner  # noqa: F401
    from adapters.base import SubmissionContext

    class _FakePage:
        url = job["application_url"]
        def is_closed(self): return False
        async def content(self): return ""
        async def wait_for_load_state(self, *a, **kw): return None

    class _FakeStagehandSession:
        pass

    ctx = SubmissionContext(
        job=job,
        resume_pdf_path=Path("/tmp/_smoke_resume.pdf"),
        cover_letter_pdf_path=Path("/tmp/_smoke_cover.pdf"),
        cover_letter_text=job.get("cover_letter_path", ""),
        application_url=job["application_url"],
        stagehand_session=_FakeStagehandSession(),
        page=_FakePage(),
        attempt_n=1,
    )

    # Cross-subtree contract checks: every field a downstream subtree
    # consumes is present and well-formed.
    if not ctx.job["applicant_profile"].get("first_name"):
        raise AssertionError("beacon_job.applicant_profile.first_name missing — submitter would fail to fill")
    if not ctx.job.get("materials_hash"):
        raise AssertionError("beacon_job.materials_hash missing — submit verify_materials_hash would fail")
    if not ctx.job.get("score") or not ctx.job.get("tier"):
        raise AssertionError("beacon_job missing score/tier — tailor sort would mis-rank")

    stage("hunt → tailor → submit data flow connected via beacon_job.json + fake_browser")
except Exception as e:
    fail("pipeline.flow", e)


elapsed = time.time() - t0
print(f"\nsmoke OK — {elapsed:.2f}s elapsed (budget: 60s)")
if elapsed > 60:
    print("WARNING: smoke exceeded 60s budget", file=sys.stderr)
    sys.exit(2)
sys.exit(0)
