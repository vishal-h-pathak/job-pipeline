"""Session G — hunting-thesis wiring: scorer context, degree gate, tier 1.5.

Covers the feat/hunt-thesis contract:
  - `profile/thesis.md` is committed and loads through
    `jobpipe.profile_loader.load_thesis`.
  - `build_profile_prompt_string` places the thesis FIRST with an
    explicit overrides-on-conflict instruction.
  - `scorer.md` accepts tier "1.5" and emits the `degree_gated` boolean.
  - `score_job` normalizes tier 1.5 / degree_gated; `should_notify`
    treats tier 1.5 like tiers 1 and 2.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from jobpipe.hunt import prompts, scorer
from jobpipe.hunt.scorer import _normalize_tier, should_notify

REPO_ROOT = Path(__file__).resolve().parent.parent
HUNT_DIR = REPO_ROOT / "jobpipe" / "hunt"

THESIS_FIXTURE = (
    "# Hunting Thesis\n\nTHESIS_MARKER_XYZZY\n\n"
    "Tier 1.5 exists. Degree-gate rule applies.\n"
)


@pytest.fixture
def fresh_profile_cache():
    """Reset the hunt prompts module's profile cache around a test."""
    prompts._PROFILE_CACHE = None
    yield
    prompts._PROFILE_CACHE = None


# ── thesis.md presence + loader wiring ──────────────────────────────────


def test_thesis_committed_in_repo_profile() -> None:
    thesis = REPO_ROOT / "profile" / "thesis.md"
    assert thesis.is_file(), "profile/thesis.md must be committed"
    body = thesis.read_text(encoding="utf-8")
    assert "Tier 1.5" in body
    assert "degree-gate" in body.lower()


def test_load_thesis_reads_profile_dir(tmp_profile) -> None:
    tmp_profile(overrides={"thesis.md": THESIS_FIXTURE})
    from jobpipe import profile_loader

    assert "THESIS_MARKER_XYZZY" in profile_loader.load_thesis()


def test_thesis_is_first_profile_doc_with_override_instruction(
    tmp_profile, fresh_profile_cache
) -> None:
    tmp_profile(overrides={"thesis.md": THESIS_FIXTURE})
    built = prompts.build_profile_prompt_string()

    assert "THESIS_MARKER_XYZZY" in built
    assert built.startswith(
        "========== thesis.md (CANONICAL"
    ), "thesis section must come first"
    profile_pos = built.index("========== profile.yml ==========")
    # The override instruction must be attached to the thesis section.
    assert "thesis.md wins" in built[:profile_pos]


def test_missing_thesis_falls_back_cleanly(tmp_profile, fresh_profile_cache) -> None:
    tmp_profile()  # fixture profile has no thesis.md
    built = prompts.build_profile_prompt_string()
    assert "thesis.md" not in built
    assert "========== profile.yml ==========" in built


# ── scorer.md prompt contract ───────────────────────────────────────────


def test_scorer_prompt_accepts_tier_1_5_and_degree_gated() -> None:
    body = (HUNT_DIR / "prompts" / "scorer.md").read_text(encoding="utf-8")
    assert '"1.5"' in body
    assert "degree_gated" in body
    # Calibration reference to the thesis's worked examples.
    assert "worked examples" in body.lower()
    # Legitimacy axis untouched.
    assert "high_confidence" in body and "suspicious" in body


# ── score_job output normalization (no live API) ────────────────────────


def _score_with_fake_response(monkeypatch, payload: dict) -> dict:
    # The scorer now routes through jobpipe.shared.llm (credits-first →
    # Max-OAuth fallback); patch that single seam so no live API call is made.
    monkeypatch.setattr(
        scorer.llm, "complete", lambda **kwargs: json.dumps(payload)
    )
    return scorer.score_job(
        title="Agent Engineer", company="X", description="d", location="Remote"
    )


def test_score_job_parses_tier_1_5_and_degree_gated(
    tmp_profile, fresh_profile_cache, monkeypatch
) -> None:
    tmp_profile(overrides={"thesis.md": THESIS_FIXTURE})
    result = _score_with_fake_response(
        monkeypatch,
        {
            "score": 8,
            "tier": "1.5",
            "degree_gated": True,
            "reasoning": "r",
            "recommended_action": "notify",
            "legitimacy": "high_confidence",
            "legitimacy_reasoning": "lr",
        },
    )
    assert result["tier"] == 1.5
    assert result["degree_gated"] is True


def test_score_job_degree_gated_defaults_false(
    tmp_profile, fresh_profile_cache, monkeypatch
) -> None:
    tmp_profile(overrides={"thesis.md": THESIS_FIXTURE})
    result = _score_with_fake_response(
        monkeypatch,
        {"score": 6, "tier": 2, "reasoning": "r", "recommended_action": "skip"},
    )
    assert result["degree_gated"] is False
    assert result["tier"] == 2


def test_normalize_tier_table() -> None:
    assert _normalize_tier(1) == 1
    assert _normalize_tier("2") == 2
    assert _normalize_tier(2.0) == 2
    assert _normalize_tier("1.5") == 1.5
    assert _normalize_tier(1.5) == 1.5
    assert _normalize_tier("disqualify") == "disqualify"
    assert _normalize_tier(None) is None


# ── should_notify tier 1.5 acceptance ───────────────────────────────────


@pytest.mark.parametrize(
    ("tier", "score", "expected"),
    [
        (1, 7, True),
        (1.5, 7, True),
        ("1.5", 7, True),
        (2, 7, True),
        (1.5, 6, False),
        (3, 7, False),
        ("disqualify", 9, False),
    ],
)
def test_should_notify_tier_matrix(tier, score, expected) -> None:
    assert should_notify({"score": score, "tier": tier}) is expected


def test_should_notify_recommended_action_short_circuits() -> None:
    assert should_notify({"recommended_action": "notify", "score": 1, "tier": 3})


# ── upsert_job writes degree_gated ──────────────────────────────────────


class _FakeJobsQuery:
    """Chainable double for client.table("jobs") in upsert_job."""

    def __init__(self, existing_rows: list[dict]):
        self._existing = existing_rows
        self._mode = None
        self.update_payload: dict | None = None
        self.upsert_payload: dict | None = None

    def select(self, _cols):
        self._mode = "select"
        return self

    def update(self, payload):
        self._mode = "update"
        self.update_payload = payload
        return self

    def upsert(self, payload, **_kw):
        self._mode = "upsert"
        self.upsert_payload = payload
        return self

    def eq(self, _col, _val):
        return self

    def execute(self):
        class _R:
            data = list(self._existing) if self._mode == "select" else []

        return _R()


class _FakeDbClient:
    def __init__(self, existing_rows: list[dict] | None = None):
        self.query = _FakeJobsQuery(existing_rows or [])

    def table(self, _name):
        return self.query


_SCORE_RESULT = {
    "score": 8,
    "tier": 1.5,
    "degree_gated": True,
    "reasoning": "r",
    "recommended_action": "notify",
    "legitimacy": "high_confidence",
    "legitimacy_reasoning": "lr",
}

_JOB = {"id": "j1", "title": "t", "company": "c", "location": "Remote",
        "description": "d", "url": "u", "source": "greenhouse"}


def test_upsert_job_insert_writes_degree_gated_and_tier(patch_db_client) -> None:
    from jobpipe.db import upsert_job

    fake = _FakeDbClient(existing_rows=[])
    patch_db_client(fake)
    upsert_job(_JOB, _SCORE_RESULT)
    payload = fake.query.upsert_payload
    assert payload is not None
    assert payload["degree_gated"] is True
    assert payload["tier"] == 1.5


def test_upsert_job_update_writes_degree_gated(patch_db_client) -> None:
    from jobpipe.db import upsert_job

    fake = _FakeDbClient(existing_rows=[{"id": "j1"}])
    patch_db_client(fake)
    upsert_job(_JOB, {**_SCORE_RESULT, "degree_gated": False})
    payload = fake.query.update_payload
    assert payload is not None
    assert payload["degree_gated"] is False


# ── notify digest handles tier 1.5 ──────────────────────────────────────


def _digest_entry(tier) -> dict:
    return {
        "job": {"title": f"T{tier}", "company": "c", "location": "Remote",
                "url": "https://x", "source": "greenhouse"},
        "score": {"score": 8, "tier": tier, "reasoning": "r"},
    }


def test_tier_key_handles_half_tier() -> None:
    from jobpipe.notify import _tier_key

    assert _tier_key(1) == 1
    assert _tier_key("1") == 1
    assert _tier_key(1.5) == 1.5
    assert _tier_key("1.5") == 1.5
    assert _tier_key("disqualify") == 99
    assert _tier_key(None) == 99


def test_digest_sorts_tier_1_5_between_1_and_2() -> None:
    from jobpipe.notify import _render_digest

    _, body = _render_digest([_digest_entry(t) for t in (2, "1.5", 1)])
    pos_1 = body.index("Tier 1 <span")
    pos_15 = body.index("Tier 1.5 <span")
    pos_2 = body.index("Tier 2 <span")
    assert pos_1 < pos_15 < pos_2


def test_degree_gated_migration_file_present() -> None:
    sql = (
        REPO_ROOT / "jobpipe" / "tailor" / "scripts" / "010_degree_gated.sql"
    ).read_text(encoding="utf-8")
    assert "degree_gated" in sql
    assert "rescored_at" in sql
    assert "DEFAULT FALSE" in sql.upper()
