"""jobpipe.tailor.paths — tailor-only filesystem paths.

PR-9 rehomed these from the deleted ``jobpipe/tailor/config.py`` shim.
They live here (a small, focused module) rather than in ``jobpipe/config.py``
because they are tailor-internal and not cross-cutting.

- ``OUTPUT_DIR`` — per-process tempdir for ephemeral diagnostic artifacts
  (browser screenshots, LaTeX compile logs). Generated application materials
  (resumes, cover letters) live in Supabase Storage, not on disk. Override
  via the ``OUTPUT_DIR`` env var when a stable path is needed (e.g. CI
  artifact upload).
- ``CANDIDATE_PROFILE_PATH`` — narrative profile prose used as LLM prompt
  context by resume / cover-letter generation. Points at the consolidated
  repo-root ``CLAUDE.md`` (PR-9 dropped the per-subpackage CLAUDE.md
  files). Structured ground truth lives in ``profile/profile.yml``; this
  path is the human-readable narrative aggregator referenced by tailor
  prompts that haven't migrated to the structured profile yet.
"""

from __future__ import annotations

import os
import tempfile
from pathlib import Path

# Resolve repo root from this file's location: jobpipe/tailor/paths.py → repo root.
_REPO_ROOT = Path(__file__).resolve().parent.parent.parent

CANDIDATE_PROFILE_PATH = _REPO_ROOT / "CLAUDE.md"

OUTPUT_DIR = Path(os.getenv("OUTPUT_DIR") or tempfile.mkdtemp(prefix="jobapp_"))
OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
