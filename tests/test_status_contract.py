"""Session E status contract — the cross-repo enum cannot drift silently.

Three invariants:

1. ``jobpipe/shared/status.json`` (the artifact the portfolio repo's
   type generator consumes) matches ``CANONICAL_STATUSES`` in
   ``jobpipe/shared/status.py``. If you edit the tuple, re-run
   ``python -m jobpipe.shared.status`` and commit the JSON.
2. Every status-transition helper in ``jobpipe.db`` emits only canonical
   statuses — exercised behaviorally against a fake client, not by
   grepping source.
3. No retired jobs.status literal survives as a string constant in
   jobpipe code. Scoped to the literals that are unambiguous
   (``ready_to_submit`` / ``submit_confirmed`` / ``tailored``):
   ``needs_review`` and ``submitted`` remain legitimate vocabulary for
   adapter recommendations and ``application_attempts.outcome``, which
   are different enums from jobs.status.

The portfolio repo holds the other half of the contract: its
``scripts/check-status-types.mjs`` (npm run build prechain) fails the
build if the generated ``JobStatus`` union drifts from this JSON.
"""

from __future__ import annotations

import ast
import json
from pathlib import Path

import pytest

import jobpipe.db as db
from jobpipe.shared.status import (
    CANONICAL_STATUSES,
    LEGACY_STATUS_MAP,
    STATUS_JSON_PATH,
    status_json_payload,
)

REPO_ROOT = Path(__file__).resolve().parent.parent
JOBPIPE_DIR = REPO_ROOT / "jobpipe"


# ── 1. status.json is generated, current, and self-consistent ─────────────

def test_status_json_matches_python_definition():
    on_disk = json.loads(STATUS_JSON_PATH.read_text(encoding="utf-8"))
    assert on_disk == status_json_payload(), (
        "jobpipe/shared/status.json is stale — regenerate with "
        "`python -m jobpipe.shared.status` and commit it"
    )


def test_legacy_map_targets_are_canonical():
    for legacy, canonical in LEGACY_STATUS_MAP.items():
        assert canonical in CANONICAL_STATUSES, (
            f"LEGACY_STATUS_MAP[{legacy!r}] -> {canonical!r} is not canonical"
        )
        assert legacy not in CANONICAL_STATUSES


def test_migration_011_check_constraint_lists_exactly_the_canonical_enum():
    sql = (JOBPIPE_DIR / "tailor" / "scripts" / "011_canonical_status.sql").read_text(
        encoding="utf-8"
    )
    constraint = sql.split("ADD CONSTRAINT jobs_status_check")[1].split(";")[0]
    in_constraint = {
        line.strip().strip(",").strip("'")
        for line in constraint.splitlines()
        if line.strip().startswith("'")
    }
    assert in_constraint == set(CANONICAL_STATUSES), (
        "011_canonical_status.sql CHECK constraint drifted from "
        "CANONICAL_STATUSES"
    )


# ── 2. db.py transitions emit only canonical statuses ─────────────────────

class _Recorder:
    """Minimal supabase-client fake that records every payload written."""

    def __init__(self):
        self.payloads: list[dict] = []

    def table(self, _name):
        return self

    def select(self, *a, **kw):
        return self

    def eq(self, *a, **kw):
        return self

    def in_(self, *a, **kw):
        return self

    def order(self, *a, **kw):
        return self

    def limit(self, *a, **kw):
        return self

    def update(self, payload):
        self.payloads.append(payload)
        return self

    def upsert(self, payload, **kw):
        self.payloads.append(payload)
        return self

    def insert(self, payload):
        self.payloads.append(payload)
        return self

    def execute(self):
        class _R:
            data = [{"id": 1, "attempt_n": 1}]
        return _R()


@pytest.fixture
def recording_db(monkeypatch):
    rec = _Recorder()
    monkeypatch.setattr(db, "_client", rec)
    monkeypatch.setattr(db, "_service_client", rec)
    return rec


def test_every_db_transition_emits_canonical_status(recording_db):
    """Call every status-writing helper; every status that would hit the
    DB must be canonical. New transitions added to db.py get covered by
    the update_job_status ValueError guard, but the direct-write paths
    (mark_submitting / mark_failed) are exercised here explicitly."""
    db.upsert_job({"id": "j1", "title": "t"}, {"score": 9})
    db.mark_preparing("j1")
    db.mark_ready_for_review("j1", resume_path="r")
    db.mark_prefilling("j1")
    db.mark_awaiting_submit("j1", screenshot_path="s.png")
    db.mark_skipped("j1", reason="nope")
    db.mark_applied("j1", clear_materials=False)
    db.mark_tailor_failed("j1", "boom", clear_materials=False)
    db.mark_submitting("j1")
    db.mark_failed("j1", "boom")

    statuses = [p["status"] for p in recording_db.payloads if "status" in p]
    assert statuses, "expected the transitions above to write statuses"
    rogue = [s for s in statuses if s not in CANONICAL_STATUSES]
    assert not rogue, f"non-canonical jobs.status written by jobpipe.db: {rogue}"


def test_update_job_status_rejects_non_canonical(recording_db):
    with pytest.raises(ValueError, match="invalid jobs.status"):
        db.update_job_status("j1", "ready_to_submit")
    assert not recording_db.payloads, "rejected status must not reach the client"


# ── 3. no retired jobs.status literal in jobpipe code ─────────────────────

# Unambiguous retired literals only — see module docstring.
_RETIRED_LITERALS = {"ready_to_submit", "submit_confirmed", "tailored"}
# Files allowed to mention them as data (the mapping itself).
_ALLOWED = {JOBPIPE_DIR / "shared" / "status.py"}


def _string_constants(py_path: Path) -> set[str]:
    tree = ast.parse(py_path.read_text(encoding="utf-8"))
    consts = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Constant) and isinstance(node.value, str):
            consts.add(node.value)
    return consts


def test_no_retired_status_literal_in_jobpipe_code():
    hits = []
    for py in JOBPIPE_DIR.rglob("*.py"):
        if py in _ALLOWED:
            continue
        found = _string_constants(py) & _RETIRED_LITERALS
        if found:
            hits.append(f"{py.relative_to(REPO_ROOT)}: {sorted(found)}")
    assert not hits, (
        "retired jobs.status literals found as string constants:\n"
        + "\n".join(hits)
    )
