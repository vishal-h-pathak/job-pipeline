"""Pin the shape and values of `application_defaults` against drift.

Three angles:

1. Real `profile/profile.yml`:
   `load_application_defaults()` must equal the frozen expected dict the
   submitter's smoke test (`smoke_greenhouse.py`) depends on. If anyone
   edits the YAML in a way that changes a form-default value, this fails
   and forces an explicit decision.

2. `tmp_profile` fixture:
   The same loader, pointed at a fresh fixture dir via
   `JOBPIPE_PROFILE_DIR`, must produce the same dict. This catches
   loader bugs that only surface under env-var override.

3. Loader output keys must match the keys smoke_greenhouse expects:
   the test pins the field set so silent additions/removals require an
   intentional update to both this test and the YAML.

The smoke script is intentionally not executed — importing it loads
Browserbase + Stagehand modules that need live creds. Asserting the
loader output is enough because `FAKE_APPLICANT` is now literally
`{**stubs, **load_application_defaults()}`.
"""

from __future__ import annotations


# Frozen at PR-2 — copy of the application_defaults block. Update this
# alongside `profile/profile.yml` when a form-default value genuinely
# changes; the test failing is the prompt to re-confirm the change is
# intentional.
EXPECTED_APPLICATION_DEFAULTS = {
    "work_authorization": "us_citizen",
    "visa_sponsorship_needed": False,
    "earliest_start_date": (
        "as early as possible; typical notice is two weeks after offer acceptance"
    ),
    "relocation_willingness": (
        "based in Atlanta, GA and strongly prefers remote or local roles; "
        "open to relocation only if remote/local options are exhausted and "
        "the role + compensation are both exceptional"
    ),
    "in_person_willingness": (
        "remote or hybrid acceptable; fully remote strongly preferred"
    ),
    "ai_policy_ack": (
        "I am transparent about my use of AI assistance in my work. I use AI\n"
        "tools (including LLMs) to accelerate drafting, research, and\n"
        "exploration, but I always keep a human in the loop: I review,\n"
        "validate, and take responsibility for all work I produce.\n"
    ),
    "previous_interview_with_company": {"anthropic": False},
}


def test_real_profile_application_defaults_matches_expected():
    """The real `profile/profile.yml` matches the frozen expected dict."""
    from jobpipe import profile_loader

    profile_loader._clear_cache_for_tests()
    actual = profile_loader.load_application_defaults()
    assert actual == EXPECTED_APPLICATION_DEFAULTS


def test_tmp_profile_loader_roundtrip(tmp_profile):
    """Loader honors `JOBPIPE_PROFILE_DIR` and parses fixture YAML."""
    from jobpipe import profile_loader

    fixture_dir = tmp_profile()
    assert profile_loader.profile_dir() == fixture_dir
    assert profile_loader.load_application_defaults() == EXPECTED_APPLICATION_DEFAULTS
    voice = profile_loader.load_voice_profile()
    assert "raw" in voice
    assert "sections" in voice
    assert "how-he-communicates" in voice["sections"]


def test_loader_keys_match_smoke_greenhouse_contract(tmp_profile):
    """Loader output keys equal the smoke contract — no silent adds/removes.

    `FAKE_APPLICANT` is `{**identity_stubs, **load_application_defaults()}`,
    so the form-default keys must be exactly `EXPECTED_APPLICATION_DEFAULTS`.
    Either side gaining or losing a key without the other catches here.
    """
    tmp_profile()
    from jobpipe import profile_loader

    defaults = profile_loader.load_application_defaults()
    assert set(defaults.keys()) == set(EXPECTED_APPLICATION_DEFAULTS.keys())
