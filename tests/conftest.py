"""Shared pytest fixtures for jobpipe.

Populated as packages migrate in PR-3..PR-10. PR-2 adds `tmp_profile()` so
tests can exercise the profile loader against a fixture profile dir
without touching the real `profile/` checked into the repo.
"""

from __future__ import annotations

from pathlib import Path
from typing import Callable

import pytest


_FIXTURE_PROFILE_YAML = """\
identity:
  name: Test Applicant
  email: test@example.invalid
  location_base: Atlanta, GA
  linkedin: linkedin.com/in/test
  website: test.example
location_and_compensation:
  base: Atlanta, GA
  remote_acceptable: true
  in_person_acceptable: hybrid only; fully remote preferred
  relocation: only if exceptional
  current_comp_usd: 100000
  target_comp_usd: "120000-140000"
archetypes:
  test_lane:
    label: Test archetype
    framing: |
      One-line framing for the fixture.
    emphasis_proof_points:
      - point one
      - point two
    tone_guidance: dry
    bullet_template: "verb / target / outcome"
application_defaults:
  work_authorization: us_citizen
  visa_sponsorship_needed: false
  earliest_start_date: as early as possible; typical notice is two weeks after offer acceptance
  relocation_willingness: based in Atlanta, GA and strongly prefers remote or local roles; open to relocation only if remote/local options are exhausted and the role + compensation are both exceptional
  in_person_willingness: remote or hybrid acceptable; fully remote strongly preferred
  ai_policy_ack: |
    I am transparent about my use of AI assistance in my work. I use AI
    tools (including LLMs) to accelerate drafting, research, and
    exploration, but I always keep a human in the loop: I review,
    validate, and take responsibility for all work I produce.
  previous_interview_with_company:
    anthropic: false
"""

_FIXTURE_VOICE_PROFILE = """\
# Voice Profile

## How He Communicates
- Direct, technically precise.

## What NOT to Do
- No flowery language.
"""

_FIXTURE_ARTICLE_DIGEST = "# Article Digest (fixture)\n\nClaim --> evidence pair.\n"

_FIXTURE_LEARNED_INSIGHTS = "<!-- Fixture placeholder -->\n# Learned Insights\n"


@pytest.fixture
def tmp_profile(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Callable[..., Path]:
    """Write a fixture profile dir and point `JOBPIPE_PROFILE_DIR` at it.

    Returns a callable that writes the fixture files into a fresh subdir of
    `tmp_path` and returns the path. Optional `overrides` dict lets a test
    replace specific filenames with custom contents:

        def test_something(tmp_profile):
            d = tmp_profile()  # default fixture
            # or
            d = tmp_profile(overrides={"profile.yml": "application_defaults: {}\\n"})
    """
    from jobpipe import profile_loader

    def _build(overrides: dict[str, str] | None = None) -> Path:
        d = tmp_path / "profile"
        d.mkdir(exist_ok=True)
        files = {
            "profile.yml": _FIXTURE_PROFILE_YAML,
            "voice-profile.md": _FIXTURE_VOICE_PROFILE,
            "article-digest.md": _FIXTURE_ARTICLE_DIGEST,
            "learned-insights.md": _FIXTURE_LEARNED_INSIGHTS,
        }
        if overrides:
            files.update(overrides)
        for name, body in files.items():
            (d / name).write_text(body, encoding="utf-8")
        monkeypatch.setenv("JOBPIPE_PROFILE_DIR", str(d))
        profile_loader._clear_cache_for_tests()
        return d

    yield _build

    profile_loader._clear_cache_for_tests()
