"""
storage.py — Supabase Storage helpers for the submitter.

Uploads review screenshots back into the job-materials bucket. The download
helpers (``download_to_tmp`` and ``download_bytes``) moved to
``jobpipe.shared.storage`` in PR-1; they are re-exported below for backward
compatibility with this module's existing callers.
"""

from __future__ import annotations

from db import service_client
from jobpipe.shared.storage import (  # noqa: F401  PR-1 re-exports
    download_bytes,
    download_to_tmp,
)

BUCKET = "job-materials"


def upload_review_screenshot(job_id: str, label: str, png_bytes: bytes) -> str:
    """Upload a review-time screenshot; return the storage key."""
    key = f"{job_id}/review/{label}.png"
    service_client.storage.from_(BUCKET).upload(
        key,
        png_bytes,
        file_options={"content-type": "image/png", "upsert": "true"},
    )
    return key
