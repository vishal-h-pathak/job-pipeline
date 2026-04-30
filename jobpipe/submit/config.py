"""jobpipe.submit.config — submit-side env loader.

PR-6 established a fail-loud import-time check on required secrets so
the legacy Browserbase runner crashed before polling started. PR-15
softens that for the **Browserbase** vars only: tailoring code reaches
this module transitively (via ``tailor/url_resolver`` →
``submit/config``) on the Phase 3 dashboard-triggered path, where
Browserbase is never opened, so requiring its credentials at import
time is wrong. Supabase + Anthropic still fail loud — those are needed
by every code path that imports this module.

Browserbase access goes through getters now:

    >>> from jobpipe.submit.config import get_browserbase_api_key
    >>> client = AsyncStagehand(
    ...     browserbase_api_key=get_browserbase_api_key(), ...
    ... )

The getter raises ``RuntimeError`` with a helpful message when the
var isn't set — giving the legacy submit path the same loud-fail
behavior it had before, deferred from import time to first use.

For everything else (``POLL_INTERVAL_SECONDS``, ``MAX_ATTEMPTS_PER_JOB``,
``ATS_CONFIDENCE_MIN``, ``AUTO_SUBMIT_THRESHOLD``, ``REVIEW_DASHBOARD_URL``,
``HEADLESS``, ``SESSION_BUDGET_SECONDS``, …), import directly from
``jobpipe.config``. PR-9 removed the per-subtree re-export plumbing
that PR-8 had introduced as a shim layer; the only re-export kept
here is the ``CLAUDE_MODEL`` alias because submit code reads
``CLAUDE_MODEL`` and the canonical name is ``SUBMITTER_CLAUDE_MODEL``.
"""
from __future__ import annotations

import os
from pathlib import Path

from dotenv import load_dotenv

from jobpipe.config import require_env
from jobpipe.config import SUBMITTER_CLAUDE_MODEL as CLAUDE_MODEL  # noqa: F401

# Load a submit-local .env if present, preserving PR-6 behavior. Safe
# no-op in production where env vars come from the process environment;
# load_dotenv does not override already-set vars by default.
_ENV_PATH = Path(__file__).resolve().parent / ".env"
if _ENV_PATH.exists():
    load_dotenv(_ENV_PATH)

# ── Required credentials (fail loud at import time per PR-6) ──────────────
SUPABASE_URL              = require_env("SUPABASE_URL")
SUPABASE_KEY              = require_env("SUPABASE_KEY")
SUPABASE_SERVICE_ROLE_KEY = require_env("SUPABASE_SERVICE_ROLE_KEY")
ANTHROPIC_API_KEY         = require_env("ANTHROPIC_API_KEY")

# ── Browserbase (lazy fail-loud per PR-15) ────────────────────────────────
# Soft defaults at import time so the tailor-only Phase 3 path doesn't
# need Browserbase env vars set on the runner. The legacy submit path
# opens a Browserbase session via jobpipe/submit/browser/session.py —
# that file calls the getters below at the point of use, which raise
# RuntimeError when the vars aren't set.

_BROWSERBASE_API_KEY: str    = os.environ.get("BROWSERBASE_API_KEY", "")
_BROWSERBASE_PROJECT_ID: str = os.environ.get("BROWSERBASE_PROJECT_ID", "")


def get_browserbase_api_key() -> str:
    """Return ``BROWSERBASE_API_KEY`` or raise ``RuntimeError`` if unset."""
    if not _BROWSERBASE_API_KEY:
        raise RuntimeError(
            "BROWSERBASE_API_KEY not set; required for the legacy submit "
            "path (jobpipe.submit.browser.session). See "
            "jobpipe/submit/.env.example."
        )
    return _BROWSERBASE_API_KEY


def get_browserbase_project_id() -> str:
    """Return ``BROWSERBASE_PROJECT_ID`` or raise ``RuntimeError`` if unset."""
    if not _BROWSERBASE_PROJECT_ID:
        raise RuntimeError(
            "BROWSERBASE_PROJECT_ID not set; required for the legacy submit "
            "path (jobpipe.submit.browser.session). See "
            "jobpipe/submit/.env.example."
        )
    return _BROWSERBASE_PROJECT_ID
