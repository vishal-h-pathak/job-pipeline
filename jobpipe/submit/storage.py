"""
storage.py — Supabase Storage helpers for the submitter.

Uploads review screenshots back into the job-materials bucket. The download
helpers (``download_to_tmp`` and ``download_bytes``) moved to
``jobpipe.shared.storage`` in PR-1; they are re-exported below for backward
compatibility with this module's existing callers.
"""

from __future__ import annotations

# Module reference, not ``from jobpipe.db import service_client``: the
# from-import resolves jobpipe.db's lazy __getattr__ at import time,
# constructing a Supabase client (and raising in secretless CI). Going
# through the module defers that to first call.
from jobpipe import db

# PR-1 moved download_to_tmp / download_bytes into jobpipe.shared.storage to
# unify the previously-divergent applicant + submitter implementations. Re-
# export them here so ``import storage`` from runner.py (and any other
# unprefixed-import callsite inside the submit subtree) keeps working — the
# stated intent of this shim per the module docstring.
from jobpipe.shared.storage import download_bytes, download_to_tmp  # noqa: F401

BUCKET = "job-materials"


def upload_review_screenshot(job_id: str, label: str, png_bytes: bytes) -> str:
    """Upload a review-time screenshot; return the storage key."""
    key = f"{job_id}/review/{label}.png"
    db.service_client.storage.from_(BUCKET).upload(
        key,
        png_bytes,
        file_options={"content-type": "image/png", "upsert": "true"},
    )
    return key
