"""
config.py — Environment-loaded settings for job-submitter.

Single import point for all env-driven values. Fails loudly on missing required
secrets at import time so misconfiguration is caught before any polling starts.
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import Final

from dotenv import load_dotenv

from jobpipe.config import require_env  # PR-6: shared env-var checker.

# Load .env from the package root if present. Safe no-op in production where
# env vars come from the process environment.
_ENV_PATH = Path(__file__).resolve().parent / ".env"
if _ENV_PATH.exists():
    load_dotenv(_ENV_PATH)


# ── Required credentials ──────────────────────────────────────────────────

SUPABASE_URL: Final[str]              = require_env("SUPABASE_URL")
SUPABASE_KEY: Final[str]              = require_env("SUPABASE_KEY")
SUPABASE_SERVICE_ROLE_KEY: Final[str] = require_env("SUPABASE_SERVICE_ROLE_KEY")

BROWSERBASE_API_KEY: Final[str]    = require_env("BROWSERBASE_API_KEY")
BROWSERBASE_PROJECT_ID: Final[str] = require_env("BROWSERBASE_PROJECT_ID")

ANTHROPIC_API_KEY: Final[str] = require_env("ANTHROPIC_API_KEY")


# ── Tuneable policy knobs ─────────────────────────────────────────────────

CLAUDE_MODEL: Final[str] = os.environ.get("CLAUDE_MODEL", "claude-sonnet-4-6")

POLL_INTERVAL_SECONDS: Final[int]     = int(os.environ.get("POLL_INTERVAL_SECONDS", "60"))
MAX_CONCURRENT_SUBMISSIONS: Final[int] = int(os.environ.get("MAX_CONCURRENT_SUBMISSIONS", "1"))

AUTO_SUBMIT_THRESHOLD: Final[float]   = float(os.environ.get("AUTO_SUBMIT_THRESHOLD", "0.90"))
SESSION_BUDGET_SECONDS: Final[int]    = int(os.environ.get("SESSION_BUDGET_SECONDS", "240"))
MAX_ATTEMPTS_PER_JOB: Final[int]      = int(os.environ.get("MAX_ATTEMPTS_PER_JOB", "3"))
HEADLESS: Final[bool]                 = os.environ.get("HEADLESS", "true").lower() in ("true", "1", "yes")

REVIEW_DASHBOARD_URL: Final[str] = os.environ.get(
    "REVIEW_DASHBOARD_URL", "https://vishal.pa.thak.io/review"
)


# ── ATS confidence overrides ──────────────────────────────────────────────
# Per-adapter minimum confidence for auto-submit. Jobs below this route to
# needs_review regardless of AUTO_SUBMIT_THRESHOLD.
ATS_CONFIDENCE_MIN: Final[dict[str, float]] = {
    "greenhouse":        0.90,
    "lever":             0.90,
    "ashby":             0.90,
    "workday":           0.85,
    "icims":             0.85,
    "smartrecruiters":   0.85,
    "linkedin":          1.01,  # never auto-submit
    "generic":           0.90,
}
