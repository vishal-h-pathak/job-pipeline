"""Shared pytest fixtures for jobpipe.

PR-10 promoted the inline `_FIXTURE_PROFILE_YAML` literal and the
`_FakePage`/`_FakeLocator`/`_FakeStagehandSession` helpers (previously
duplicated as locals inside `jobpipe/submit/tests/test_scaffold.py`)
into one place. Profile bytes live on disk under `tests/fixtures/profile/`
so any test or harness can point `JOBPIPE_PROFILE_DIR` at the same
ground truth. The fake-browser surface is intentionally pure-Python —
no `playwright` / `browserbase` imports at module load — so unit tests
collect cleanly in environments where those packages are absent
(CI matrix slices, fresh checkouts).

Fixtures provided:

  tmp_profile        — factory: writes a fixture profile dir, sets env, returns Path
  beacon_job         — deepcopy of `tests/fixtures/beacon_job.json` per test
  fake_form_html     — `tests/fixtures/fake_form.html` contents as str
  fake_page          — factory: yields a `_FakePage` (Playwright-shaped surface)
  fake_locator       — factory: yields a `_FakeLocator`
  fake_browser       — factory: yields a `_FakeStagehandSession`
"""

from __future__ import annotations

import copy
import json
import shutil
from pathlib import Path
from typing import Callable

import pytest


_FIXTURES_DIR = Path(__file__).resolve().parent / "fixtures"
_PROFILE_FIXTURE_DIR = _FIXTURES_DIR / "profile"


@pytest.fixture
def tmp_profile(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Callable[..., Path]:
    """Copy the canonical fixture profile into a fresh tmp dir + set env.

    Returns a callable; the fixture profile lives on disk at
    `tests/fixtures/profile/` so the same bytes feed both the pytest
    fixture and `scripts/smoke.py`. `overrides` lets a test substitute
    file contents:

        def test_something(tmp_profile):
            d = tmp_profile()
            d = tmp_profile(overrides={"profile.yml": "application_defaults: {}\\n"})
    """
    from jobpipe import profile_loader

    def _build(overrides: dict[str, str] | None = None) -> Path:
        d = tmp_path / "profile"
        d.mkdir(exist_ok=True)
        for src in _PROFILE_FIXTURE_DIR.iterdir():
            if src.is_file():
                shutil.copy2(src, d / src.name)
        if overrides:
            for name, body in overrides.items():
                (d / name).write_text(body, encoding="utf-8")
        monkeypatch.setenv("JOBPIPE_PROFILE_DIR", str(d))
        profile_loader._clear_cache_for_tests()
        return d

    yield _build

    profile_loader._clear_cache_for_tests()


@pytest.fixture
def beacon_job() -> dict:
    """Return a deepcopy of the canonical beacon job row.

    Deepcopy so a test mutating nested `applicant_profile` doesn't leak
    into other tests. Source of truth: `tests/fixtures/beacon_job.json`.
    """
    raw = json.loads((_FIXTURES_DIR / "beacon_job.json").read_text(encoding="utf-8"))
    return copy.deepcopy(raw)


@pytest.fixture
def fake_form_html() -> str:
    """Return the Greenhouse+Lever-shaped fake form HTML as a string."""
    return (_FIXTURES_DIR / "fake_form.html").read_text(encoding="utf-8")


# ── Browser fakes ────────────────────────────────────────────────────────
# Pure-Python doubles for the surface that submit-side adapters poke at.
# They mirror the shape of `playwright.async_api.Page` / `Locator` and
# the Stagehand session, but import nothing — so collecting these
# fixtures in an env without Playwright/Browserbase installed never
# triggers an ImportError. Any test that needs *real* Stagehand calls
# will lazy-import inside the test body, not via these fixtures.


class _FakeLocator:
    """Minimal Playwright Locator stand-in.

    `count` controls whether `await loc.count()` reports the locator
    matched anything; `set_input_files_calls` records the file paths
    handed to `set_input_files()` so tests can assert upload behavior.
    """

    def __init__(self, count: int = 1):
        self._count = count
        self.first = self
        self.set_input_files_calls: list[str] = []

    async def count(self) -> int:
        return self._count

    async def set_input_files(self, path: str) -> None:
        self.set_input_files_calls.append(path)


class _FakePage:
    """Minimal Playwright Page stand-in.

    `file_inputs_exist=False` flips locator counts to 0 so adapters
    take their "missing required input" branch. `locator_calls`
    records every selector queried, for assertion in tests.
    """

    def __init__(self, file_inputs_exist: bool = True):
        self._file_inputs_exist = file_inputs_exist
        self.locator_calls: list[str] = []
        self._locator = _FakeLocator(count=1 if file_inputs_exist else 0)
        self.url = "https://example.invalid/fake-form"

    def is_closed(self) -> bool:
        return False

    def locator(self, sel: str) -> _FakeLocator:
        self.locator_calls.append(sel)
        return self._locator

    async def content(self) -> str:
        return ""

    async def wait_for_load_state(self, *args, **kwargs) -> None:
        return None


class _FakeStagehandSession:
    """Stagehand session stand-in.

    Carries a survey + per-question-answer dict so tests can wire up
    `sh_extract` monkeypatches against deterministic data. Records
    `act_calls`/`extract_calls` for assertion. The adapter helpers
    `sh_act`/`sh_extract`/`sh_execute` are usually monkeypatched
    directly at the module level, so this class is mostly a context
    object — but tests that exercise observe/act/extract through
    the session can extend it.
    """

    def __init__(
        self,
        survey: dict | None = None,
        question_answers: dict[str, dict] | None = None,
    ):
        self._survey = survey or {}
        self._answers = question_answers or {}
        self.act_calls: list[str] = []
        self.extract_calls: list[str] = []


@pytest.fixture
def fake_locator() -> Callable[..., _FakeLocator]:
    """Factory: build a `_FakeLocator` per call.

        def test_x(fake_locator):
            loc = fake_locator(count=0)  # simulates "no match"
    """
    def _build(count: int = 1) -> _FakeLocator:
        return _FakeLocator(count=count)
    return _build


@pytest.fixture
def fake_page() -> Callable[..., _FakePage]:
    """Factory: build a `_FakePage` per call.

        def test_x(fake_page):
            page = fake_page(file_inputs_exist=False)
    """
    def _build(file_inputs_exist: bool = True) -> _FakePage:
        return _FakePage(file_inputs_exist=file_inputs_exist)
    return _build


@pytest.fixture
def fake_browser() -> Callable[..., _FakeStagehandSession]:
    """Factory: build a `_FakeStagehandSession` per call.

        def test_x(fake_browser):
            sess = fake_browser(survey={"first_name_present": True, ...})
    """
    def _build(
        survey: dict | None = None,
        question_answers: dict[str, dict] | None = None,
    ) -> _FakeStagehandSession:
        return _FakeStagehandSession(
            survey=survey, question_answers=question_answers,
        )
    return _build
