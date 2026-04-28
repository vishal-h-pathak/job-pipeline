"""jobpipe.submit.config — PR-8 shim. See ``jobpipe/config.py`` for the
canonical defaults.

Submit-side preserves the **fail-loud** import-time check on required
secrets that PR-6 established: missing ``SUPABASE_URL`` /
``BROWSERBASE_API_KEY`` / etc. raises at import time so the runner
crashes before any polling starts. Soft-default reads from
``jobpipe.config`` are still available via re-export and module-level
``__getattr__``.
"""
from __future__ import annotations

from pathlib import Path

from dotenv import load_dotenv

from jobpipe.config import (  # noqa: F401  PR-8: shared cross-subtree exports
    ATS_CONFIDENCE_MIN,
    AUTO_SUBMIT_THRESHOLD,
    HEADLESS,
    MAX_ATTEMPTS_PER_JOB,
    MAX_CONCURRENT_SUBMISSIONS,
    POLL_INTERVAL_SECONDS,
    REVIEW_DASHBOARD_URL,
    SESSION_BUDGET_SECONDS,
    require_env,
)
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


def __getattr__(name: str):
    """Forward any other attribute to ``jobpipe.config`` so submit code
    can lazily reach cross-subtree constants without re-listing them
    here every time the canonical module grows."""
    import jobpipe.config as _canonical
    try:
        return getattr(_canonical, name)
    except AttributeError as exc:
        raise AttributeError(name) from exc
