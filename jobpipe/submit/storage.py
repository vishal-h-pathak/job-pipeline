"""
storage.py — Supabase Storage helpers for the submitter.

Downloads resume / cover-letter PDFs into tmp files for the browser to upload,
and uploads review screenshots back into the same bucket.

This module mirrors the shape of job-applicant/storage.py so migration is
straightforward, but is read-heavy (only writes during review packet
generation).
"""

from __future__ import annotations

import logging
import tempfile
from pathlib import Path

from db import service_client

logger = logging.getLogger("submitter.storage")

BUCKET = "job-materials"


def download_to_tmp(storage_path: str, suffix: str = ".pdf") -> Path:
    """Fetch the object at storage_path into a NamedTemporaryFile; return Path."""
    res = service_client.storage.from_(BUCKET).download(storage_path)
    fd = tempfile.NamedTemporaryFile(suffix=suffix, delete=False)
    fd.write(res)
    fd.flush()
    fd.close()
    logger.debug("downloaded %s -> %s (%d bytes)", storage_path, fd.name, len(res))
    return Path(fd.name)


def download_bytes(storage_path: str) -> bytes:
    """Return the raw bytes without touching the filesystem."""
    return service_client.storage.from_(BUCKET).download(storage_path)


def upload_review_screenshot(job_id: str, label: str, png_bytes: bytes) -> str:
    """Upload a review-time screenshot; return the storage key."""
    key = f"{job_id}/review/{label}.png"
    service_client.storage.from_(BUCKET).upload(
        key,
        png_bytes,
        file_options={"content-type": "image/png", "upsert": "true"},
    )
    return key
