"""jobpipe.config — process-environment helpers shared by all sub-packages.

PR-8 promotes the per-subtree config.py files
(``jobpipe/{hunt,tailor,submit}/config.py``) into a single canonical entry
point. Per-subtree config.py files remain as thin shims for the
unprefixed-import pattern PR-3/4/5 set up; they re-export from this
module via module-level ``__getattr__``.

Behavior contract (preserves PR-6's split):
- Every secret name is resolvable here as a **soft default** (empty string
  or numeric default) so this module can be imported in tests / CI without
  the full secret set.
- The submit subtree's ``jobpipe/submit/config.py`` shim wraps the same
  names in :func:`require_env` at import time so the runner still fails
  loud on missing secrets.

CLAUDE_MODEL is intentionally **NOT** unified here — see
``TAILOR_CLAUDE_MODEL`` / ``SUBMITTER_CLAUDE_MODEL``. Unifying would have
silently changed LLM output (resumes, cover letters, post-submit confirm)
on a structural-consolidation PR. Future unify is one env flip away via
the ``CLAUDE_MODEL`` fallback.

Subtree-specific path constants (``PROJECT_ROOT``, ``TEMPLATES_DIR``,
``OUTPUT_DIR``, ``CANDIDATE_PROFILE_PATH``) stay under their respective
subtree shim — they're not cross-cutting.
"""

from __future__ import annotations

import os
from typing import Final, Literal

from dotenv import load_dotenv

# Idempotent: load_dotenv() does not override already-set env vars by
# default, so submit/tailor shims that call it again are no-ops.
load_dotenv()


# ── Cross-subtree env helpers ─────────────────────────────────────────────

def require_env(name: str) -> str:
    """Return ``os.environ[name]`` or raise with a uniform error message.

    PR-6 promoted this from ``jobpipe/submit/config.py``; PR-8 keeps it
    here as the shared strict-env helper for the whole pipeline. The
    error message points the operator at ``.env.example`` so the missing
    key has an obvious place to look up the expected value/format.
    """
    val = os.environ.get(name)
    if not val:
        raise RuntimeError(
            f"Missing required env var: {name}. See .env.example."
        )
    return val


def _bool(name: str, default: str = "true") -> bool:
    """Parse a boolean env var. Accepts ``true|1|yes`` (case-insensitive)."""
    return os.environ.get(name, default).strip().lower() in ("true", "1", "yes")


# ── Supabase (soft defaults) ──────────────────────────────────────────────
# Submit subtree wraps these names in require_env() at startup; tailor
# tolerates empty values so the package can be imported without secrets
# during tests.
SUPABASE_URL: Final[str]              = os.environ.get("SUPABASE_URL", "")
SUPABASE_KEY: Final[str]              = os.environ.get("SUPABASE_KEY", "")
SUPABASE_SERVICE_ROLE_KEY: Final[str] = (
    os.environ.get("SUPABASE_SERVICE_ROLE_KEY") or SUPABASE_KEY
)


# ── Anthropic (soft default) ──────────────────────────────────────────────
ANTHROPIC_API_KEY: Final[str] = os.environ.get("ANTHROPIC_API_KEY", "")


# ── Claude model selection (PR-8: per-subtree to preserve LLM parity) ─────
# Tailor and submitter use Sonnet for different tasks (resume / cover-letter
# authorship vs. post-submit confirm-page analysis). PR-8 refused to unify
# the default because it would silently change LLM output. Each subtree has
# its own override; CLAUDE_MODEL acts as a global fallback for either when
# set, so a future unify is one env flip away. Resolution order: direct
# subtree override → CLAUDE_MODEL fallback → subtree-specific default.
_CLAUDE_MODEL_FALLBACK: Final[str | None] = os.environ.get("CLAUDE_MODEL")

TAILOR_CLAUDE_MODEL: Final[str] = (
    os.environ.get("TAILOR_CLAUDE_MODEL")
    or _CLAUDE_MODEL_FALLBACK
    or "claude-sonnet-4-20250514"
)
SUBMITTER_CLAUDE_MODEL: Final[str] = (
    os.environ.get("SUBMITTER_CLAUDE_MODEL")
    or _CLAUDE_MODEL_FALLBACK
    or "claude-sonnet-4-6"
)
# Backward-compat: ``CLAUDE_MODEL`` was the canonical name in
# jobpipe/submit/config.py pre-PR-8. Submit-side reads still resolve here
# and track SUBMITTER_CLAUDE_MODEL. Tailor callers should read
# TAILOR_CLAUDE_MODEL explicitly.
CLAUDE_MODEL: Final[str] = SUBMITTER_CLAUDE_MODEL


# ── Browserbase (submit-only at runtime) ──────────────────────────────────
# Defined here per the PR-8 spec ("all env constants from all three repos").
# Soft default — submit/config.py shim re-promotes via require_env.
BROWSERBASE_API_KEY: Final[str]    = os.environ.get("BROWSERBASE_API_KEY", "")
BROWSERBASE_PROJECT_ID: Final[str] = os.environ.get("BROWSERBASE_PROJECT_ID", "")


# ── Polling intervals (separate knobs — submit is per-second, tailor per-minute) ─
POLL_INTERVAL_SECONDS: Final[int] = int(os.environ.get("POLL_INTERVAL_SECONDS", "60"))
POLL_INTERVAL_MINUTES: Final[int] = int(os.environ.get("POLL_INTERVAL_MINUTES", "120"))


# ── Submitter policy knobs ────────────────────────────────────────────────
MAX_CONCURRENT_SUBMISSIONS: Final[int] = int(os.environ.get("MAX_CONCURRENT_SUBMISSIONS", "1"))
AUTO_SUBMIT_THRESHOLD: Final[float]    = float(os.environ.get("AUTO_SUBMIT_THRESHOLD", "0.90"))
SESSION_BUDGET_SECONDS: Final[int]     = int(os.environ.get("SESSION_BUDGET_SECONDS", "240"))
MAX_ATTEMPTS_PER_JOB: Final[int]       = int(os.environ.get("MAX_ATTEMPTS_PER_JOB", "3"))
HEADLESS: Final[bool]                  = _bool("HEADLESS", "true")
REVIEW_DASHBOARD_URL: Final[str]       = os.environ.get(
    "REVIEW_DASHBOARD_URL", "https://vishal.pa.thak.io/review"
)
# Per-adapter minimum confidence for auto-submit. Jobs below this route
# to needs_review regardless of AUTO_SUBMIT_THRESHOLD.
ATS_CONFIDENCE_MIN: Final[dict[str, float]] = {
    "greenhouse":      0.90,
    "lever":           0.90,
    "ashby":           0.90,
    "workday":         0.85,
    "icims":           0.85,
    "smartrecruiters": 0.85,
    "linkedin":        1.01,  # never auto-submit
    "generic":         0.90,
}


# ── Tailor policy knobs ───────────────────────────────────────────────────
HUMAN_APPROVAL_REQUIRED: Final[bool] = _bool("HUMAN_APPROVAL_REQUIRED", "true")
AUTO_SUBMIT_ENABLED: Final[bool]     = _bool("AUTO_SUBMIT_ENABLED", "false")
AUTO_SUBMIT_MIN_SCORE: Final[int]    = int(os.environ.get("AUTO_SUBMIT_MIN_SCORE", "9"))
AUTO_SUBMIT_MIN_TIER: Final[int]     = int(os.environ.get("AUTO_SUBMIT_MIN_TIER", "1"))


# ── Notify (cockpit deep-link base) ───────────────────────────────────────
# Used by jobpipe.notify.cockpit_url() to build deep links into the
# dashboard. Override via PORTFOLIO_BASE_URL for staging / preview deploys.
PORTFOLIO_BASE_URL: Final[str] = os.environ.get(
    "PORTFOLIO_BASE_URL", "https://vishal.pa.thak.io"
).rstrip("/")


# ── Hunter mode + location filters (promoted from jobpipe/hunt/config.py) ─

Mode = Literal["local_remote", "us_wide"]
DEFAULT_MODE: Mode = "local_remote"

# Sentinel allowing the orchestrator to set the mode at startup so each
# source module can read it without re-parsing CLI args.
_ACTIVE_MODE: Mode | None = None


def set_mode(mode: Mode) -> None:
    """Override the active hunter mode for this process."""
    global _ACTIVE_MODE
    if mode not in ("local_remote", "us_wide"):
        raise ValueError(f"unknown HUNTER_MODE: {mode!r}")
    _ACTIVE_MODE = mode


def get_mode() -> Mode:
    """Return the active hunter mode.

    Resolution order:
        1. set_mode() override
        2. HUNTER_MODE env var
        3. DEFAULT_MODE
    """
    if _ACTIVE_MODE is not None:
        return _ACTIVE_MODE
    env = os.environ.get("HUNTER_MODE", "").strip().lower()
    if env in ("local_remote", "us_wide"):
        return env  # type: ignore[return-value]
    return DEFAULT_MODE


# Atlanta-area locations recognised when filtering Greenhouse/Lever boards
# in local_remote mode.
LOCAL_LOCATION_SUBSTRINGS = (
    "atlanta",
    "georgia",
    "ga,",
    " ga",
    "ga/",
)

REMOTE_LOCATION_SUBSTRINGS = (
    "remote",
    "anywhere",
    "distributed",
    "work from home",
    "wfh",
    "us-remote",
    "us remote",
    "global",
)


def is_local_or_remote(location: str | None) -> bool:
    """True if the location string looks like Atlanta or a remote role.

    The check is intentionally generous — Greenhouse boards have wildly
    inconsistent location strings ("Remote - US", "Atlanta, GA / Remote",
    "United States (Remote)") so we look for any matching substring rather
    than requiring an exact match. Empty / null locations are treated as
    "unknown but probably ok" so they aren't dropped silently.
    """
    if not location:
        return True
    s = location.lower()
    if any(needle in s for needle in REMOTE_LOCATION_SUBSTRINGS):
        return True
    if any(needle in s for needle in LOCAL_LOCATION_SUBSTRINGS):
        return True
    return False


def location_filter_enabled() -> bool:
    """Should sources filter their results down to Atlanta/Remote?"""
    return get_mode() == "local_remote"
