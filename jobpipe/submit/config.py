"""jobpipe.submit.config — submit-side fail-loud env loader.

Submit preserves the **fail-loud** import-time check on required secrets
that PR-6 established: missing ``SUPABASE_URL`` / ``BROWSERBASE_API_KEY``
/ etc. raises at import time so the runner crashes before any polling
starts.

For everything else (``POLL_INTERVAL_SECONDS``, ``MAX_ATTEMPTS_PER_JOB``,
``ATS_CONFIDENCE_MIN``, ``AUTO_SUBMIT_THRESHOLD``, ``REVIEW_DASHBOARD_URL``,
``HEADLESS``, ``SESSION_BUDGET_SECONDS``, …), import directly from
``jobpipe.config``. PR-9 removed the per-subtree re-export plumbing that
PR-8 had introduced as a shim layer; the only re-export kept here is the
``CLAUDE_MODEL`` alias because submit code reads ``CLAUDE_MODEL`` and the
canonical name is ``SUBMITTER_CLAUDE_MODEL``.
"""
from __future__ import annotations

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
BROWSERBASE_API_KEY       = require_env("BROWSERBASE_API_KEY")
BROWSERBASE_PROJECT_ID    = require_env("BROWSERBASE_PROJECT_ID")
ANTHROPIC_API_KEY         = require_env("ANTHROPIC_API_KEY")
