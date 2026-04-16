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
OUTPUT_DIR = PROJECT_ROOT / "output"
OUTPUT_DIR.mkdir(exist_ok=True)

# ── Supabase ─────────────────────────────────────────────────────────────────
SUPABASE_URL = os.getenv("SUPABASE_URL", "")
SUPABASE_KEY = os.getenv("SUPABASE_KEY", "")

# ── Claude API ───────────────────────────────────────────────────────────────
ANTHROPIC_API_KEY = os.getenv("ANTHROPIC_API_KEY", "")
CLAUDE_MODEL = "claude-sonnet-4-6"

# ── Notifications ────────────────────────────────────────────────────────────
# Notifications are written to Supabase and displayed on vishal.pa.thak.io
# No external push service needed

# ── Agent behavior ───────────────────────────────────────────────────────────
POLL_INTERVAL_MINUTES = int(os.getenv("POLL_INTERVAL_MINUTES", "5"))
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
