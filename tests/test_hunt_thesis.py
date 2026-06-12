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


class _FakeBlock:
    def __init__(self, text: str) -> None:
        self.text = text


class _FakeResponse:
    def __init__(self, text: str) -> None:
        self.content = [_FakeBlock(text)]


class _FakeMessages:
    def __init__(self, text: str) -> None:
        self._text = text

    def create(self, **kwargs):
        return _FakeResponse(self._text)


class _FakeClient:
    def __init__(self, text: str) -> None:
        self.messages = _FakeMessages(text)


def _score_with_fake_response(monkeypatch, payload: dict) -> dict:
    monkeypatch.setattr(
        scorer, "_client_lazy", lambda: _FakeClient(json.dumps(payload))
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
