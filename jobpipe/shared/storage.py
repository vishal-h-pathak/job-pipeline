"""
jobpipe.shared.storage — Supabase Storage download helpers shared by hunt /
tailor / submit.

Hosts the unified ``download_to_tmp`` and ``download_bytes`` (formerly
duplicated across job-applicant/storage.py and job-submitter/storage.py with
divergent bodies). The unified body adopts:

  - the applicant version's defensive empty-download check + try/except
    cleanup (resume PDFs are user-visible; silent empty downloads would
    corrupt a submission)
  - the submitter version's optional ``suffix`` parameter and debug log

Other storage operations (uploads, deletes, signed-URL generation, screenshot
upload) remain in their original modules until later PRs.

The Supabase client is created lazily inside ``_get_client`` so importing this
module does not require ``supabase`` to be installed — only calling a function
does. This keeps ``pytest --collect-only`` working in environments where the
runtime SDK isn't installed.
"""

from __future__ import annotations

import logging
import os
import tempfile
from pathlib import Path
from typing import Any, Optional

logger = logging.getLogger("jobpipe.shared.storage")

BUCKET = "job-materials"

_client: Any = None


def _get_client() -> Any:
    """Return a memoized Supabase service-role client; create on first call."""
    global _client
    if _client is not None:
        return _client

    url = os.getenv("SUPABASE_URL", "")
    key = os.getenv("SUPABASE_SERVICE_ROLE_KEY") or os.getenv("SUPABASE_KEY", "")
    if not (url and key):
        raise RuntimeError(
            "Supabase storage client not configured: set SUPABASE_URL and "
            "SUPABASE_SERVICE_ROLE_KEY (or SUPABASE_KEY) in env."
        )

    # Lazy import so `pytest --collect-only` works without the supabase SDK.
    from supabase import create_client  # type: ignore[import-not-found]

    _client = create_client(url, key)
    return _client


def download_bytes(storage_path: str) -> bytes:
    """Return the raw bytes at ``storage_path`` without touching the filesystem."""
    return _get_client().storage.from_(BUCKET).download(storage_path)


def download_to_tmp(storage_path: str, suffix: Optional[str] = None) -> Path:
    """Download a Supabase Storage object to a NamedTemporaryFile.

    Unified body adopted in PR-1 (replaces divergent copies under
    job-applicant/storage.py and job-submitter/storage.py):

      - raises RuntimeError on empty data (was silent in submitter version)
      - cleans up the orphaned temp file if the write fails (was missing in
        submitter version)
      - ``suffix`` defaults to ``Path(storage_path).suffix`` then ``.pdf``,
        so callers no longer need to pass it explicitly (was required in
        submitter version)

    Caller is responsible for deleting the file when done (or letting the OS
    reclaim ``/tmp``).
    """
    data = _get_client().storage.from_(BUCKET).download(storage_path)
    if not data:
        raise RuntimeError(f"Empty download for storage_path={storage_path}")

    suffix = suffix or Path(storage_path).suffix or ".pdf"
    fd, name = tempfile.mkstemp(prefix="jobpipe_", suffix=suffix)
    try:
        with os.fdopen(fd, "wb") as f:
            f.write(data)
    except Exception:
        os.unlink(name)
        raise
    logger.debug("downloaded %s -> %s (%d bytes)", storage_path, name, len(data))
    return Path(name)
