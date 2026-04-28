"""jobpipe.tailor.config — PR-8 shim. See ``jobpipe/config.py`` for the
canonical defaults.

Tailor-side keeps the **soft-default** policy (PR-6 contract): missing
secrets resolve to empty strings so the package can be imported in
tests without environment config. The fail-loud version lives in
``jobpipe/submit/config.py``.

Tailor-only path constants stay here because they're not cross-cutting:
``PROJECT_ROOT``, ``TEMPLATES_DIR``, ``OUTPUT_DIR``,
``CANDIDATE_PROFILE_PATH``.
"""
from __future__ import annotations

import os
import tempfile as _tempfile
from pathlib import Path

from jobpipe.config import (  # noqa: F401  PR-8 re-exports
    ANTHROPIC_API_KEY,
    AUTO_SUBMIT_ENABLED,
    AUTO_SUBMIT_MIN_SCORE,
    AUTO_SUBMIT_MIN_TIER,
    HUMAN_APPROVAL_REQUIRED,
    POLL_INTERVAL_MINUTES,
    SUPABASE_KEY,
    SUPABASE_SERVICE_ROLE_KEY,
    SUPABASE_URL,
)
from jobpipe.config import TAILOR_CLAUDE_MODEL as CLAUDE_MODEL  # noqa: F401

# ── Tailor-only paths (not promoted to jobpipe/config.py) ────────────────
PROJECT_ROOT = Path(__file__).parent
TEMPLATES_DIR = PROJECT_ROOT / "templates"

# Generated materials (resumes, cover letters) live in Supabase Storage —
# never on disk. OUTPUT_DIR exists only for ephemeral diagnostic
# artifacts (browser screenshots, LaTeX compile logs) which are safe to
# lose. Default to a per-process tempdir so nothing persists across runs.
OUTPUT_DIR = Path(os.getenv("OUTPUT_DIR") or _tempfile.mkdtemp(prefix="jobapp_"))
OUTPUT_DIR.mkdir(exist_ok=True)

# Candidate profile (legacy aggregator file). The canonical user-layer
# ground truth lives in `profile/` (J-10 DATA_CONTRACT). This path is
# kept for tooling that hasn't migrated yet.
CANDIDATE_PROFILE_PATH = PROJECT_ROOT / "CLAUDE.md"
if not CANDIDATE_PROFILE_PATH.exists():
    alt = PROJECT_ROOT.parent / "job-hunter" / "CLAUDE.md"
    if alt.exists():
        CANDIDATE_PROFILE_PATH = alt


def __getattr__(name: str):
    """Forward any other attribute to ``jobpipe.config`` so tailor code
    can lazily reach cross-subtree constants without re-listing them
    here every time the canonical module grows."""
    import jobpipe.config as _canonical
    try:
        return getattr(_canonical, name)
    except AttributeError as exc:
        raise AttributeError(name) from exc
