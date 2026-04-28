"""
config.py — Central configuration for the job-applicant agent.
"""

import os
from pathlib import Path
from dotenv import load_dotenv

load_dotenv()

# ── Paths ────────────────────────────────────────────────────────────────────
PROJECT_ROOT = Path(__file__).parent
TEMPLATES_DIR = PROJECT_ROOT / "templates"

# Generated materials (resumes, cover letters) are stored in Supabase Storage
# — never on disk. OUTPUT_DIR now exists only for ephemeral diagnostic
# artifacts (browser screenshots, LaTeX compile logs) which are safe to lose.
# Default to a per-process tempdir so nothing persists across runs.
import tempfile as _tempfile
OUTPUT_DIR = Path(os.getenv("OUTPUT_DIR") or _tempfile.mkdtemp(prefix="jobapp_"))
OUTPUT_DIR.mkdir(exist_ok=True)

# ── Supabase ─────────────────────────────────────────────────────────────────
SUPABASE_URL = os.getenv("SUPABASE_URL", "")
SUPABASE_KEY = os.getenv("SUPABASE_KEY", "")
# Service role key is required for Storage uploads/deletes and for the
# dashboard API route that mints signed URLs. Must NOT be committed to git.
SUPABASE_SERVICE_ROLE_KEY = os.getenv("SUPABASE_SERVICE_ROLE_KEY", SUPABASE_KEY)

# ── Claude API ───────────────────────────────────────────────────────────────
ANTHROPIC_API_KEY = os.getenv("ANTHROPIC_API_KEY", "")
CLAUDE_MODEL = os.getenv("CLAUDE_MODEL", "claude-sonnet-4-20250514")

# ── Notifications ────────────────────────────────────────────────────────────
# Notifications are written to Supabase and displayed on vishal.pa.thak.io
# No external push service needed

# ── Agent behavior ───────────────────────────────────────────────────────────
POLL_INTERVAL_MINUTES = int(os.getenv("POLL_INTERVAL_MINUTES", "120"))
HUMAN_APPROVAL_REQUIRED = os.getenv("HUMAN_APPROVAL_REQUIRED", "true").lower() == "true"
AUTO_SUBMIT_ENABLED = os.getenv("AUTO_SUBMIT_ENABLED", "false").lower() == "true"
AUTO_SUBMIT_MIN_SCORE = int(os.getenv("AUTO_SUBMIT_MIN_SCORE", "9"))
AUTO_SUBMIT_MIN_TIER = int(os.getenv("AUTO_SUBMIT_MIN_TIER", "1"))

# ── Candidate profile ───────────────────────────────────────────────────────
# Symlink or copy from job-hunter/CLAUDE.md
CANDIDATE_PROFILE_PATH = PROJECT_ROOT / "CLAUDE.md"

# Fallback: try job-hunter's copy
if not CANDIDATE_PROFILE_PATH.exists():
    alt = PROJECT_ROOT.parent / "job-hunter" / "CLAUDE.md"
    if alt.exists():
        CANDIDATE_PROFILE_PATH = alt
