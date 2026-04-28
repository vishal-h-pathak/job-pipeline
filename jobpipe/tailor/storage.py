"""
storage.py — Supabase Storage wrapper for generated application materials.

All resume / cover-letter PDFs live in a private bucket `job-materials`.
Path convention: `{job_id}/{kind}.pdf`  where kind ∈ {"resume", "cover_letter"}.

Requires SUPABASE_SERVICE_ROLE_KEY in env (the anon key can't delete or sign).
"""

from __future__ import annotations

import logging
import os
import tempfile
from pathlib import Path
from typing import Literal, Optional

from supabase import create_client
from config import SUPABASE_URL

logger = logging.getLogger("storage")

BUCKET = "job-materials"
Kind = Literal["resume", "cover_letter"]

_SERVICE_KEY = os.getenv("SUPABASE_SERVICE_ROLE_KEY") or os.getenv("SUPABASE_KEY", "")
if not _SERVICE_KEY:
    logger.warning(
        "Neither SUPABASE_SERVICE_ROLE_KEY nor SUPABASE_KEY set — "
        "Storage uploads/deletes will fail."
    )

_client = create_client(SUPABASE_URL, _SERVICE_KEY) if SUPABASE_URL and _SERVICE_KEY else None


def _require_client():
    if _client is None:
        raise RuntimeError(
            "Supabase storage client not initialized (missing SUPABASE_URL or "
            "SUPABASE_SERVICE_ROLE_KEY). Check .env."
        )
    return _client


def _path_for(job_id: str | int, kind: Kind) -> str:
    return f"{job_id}/{kind}.pdf"


def upload_pdf(job_id: str | int, kind: Kind, pdf_bytes: bytes) -> str:
    """
    Upload a PDF to the job-materials bucket. Overwrites any existing file.

    Returns the storage path (e.g. "42/resume.pdf").
    """
    client = _require_client()
    path = _path_for(job_id, kind)
    storage = client.storage.from_(BUCKET)

    # Upsert semantics: delete if exists, then insert. The supabase-py v2 SDK
    # also supports `upsert=True` via file_options.
    try:
        storage.upload(
            path=path,
            file=pdf_bytes,
            file_options={
                "content-type": "application/pdf",
                "upsert": "true",
            },
        )
    except Exception as e:
        # Older SDKs throw on duplicate; fall back to remove-then-upload.
        logger.debug(f"upload with upsert failed ({e!r}); trying remove-then-upload")
        try:
            storage.remove([path])
        except Exception:
            pass
        storage.upload(
            path=path,
            file=pdf_bytes,
            file_options={"content-type": "application/pdf"},
        )

    logger.info(f"Uploaded {path} ({len(pdf_bytes)} bytes) to bucket={BUCKET}")
    return path


def upload_prefill_screenshot(job_id: str | int, png_bytes: bytes) -> str:
    """Upload the post-prefill screenshot the dashboard cockpit (M-6) renders.

    Returns the storage path (e.g. "42/prefill.png"). Same bucket as PDFs;
    different filename so it doesn't collide with resume / cover_letter
    objects.
    """
    client = _require_client()
    path = f"{job_id}/prefill.png"
    storage = client.storage.from_(BUCKET)
    try:
        storage.upload(
            path=path,
            file=png_bytes,
            file_options={
                "content-type": "image/png",
                "upsert": "true",
            },
        )
    except Exception as e:
        logger.debug(f"prefill upload with upsert failed ({e!r}); retrying")
        try:
            storage.remove([path])
        except Exception:
            pass
        storage.upload(
            path=path,
            file=png_bytes,
            file_options={"content-type": "image/png"},
        )
    logger.info(f"Uploaded {path} ({len(png_bytes)} bytes) to bucket={BUCKET}")
    return path


def get_signed_url(path: str, expires_in: int = 3600) -> str:
    """
    Create a short-lived signed URL (default 1 hour) the dashboard can render.
    """
    client = _require_client()
    res = client.storage.from_(BUCKET).create_signed_url(path, expires_in)
    # supabase-py v2 returns {"signedURL": "..."} or {"signed_url": "..."}.
    url = res.get("signedURL") or res.get("signed_url") or res.get("signedUrl")
    if not url:
        raise RuntimeError(f"create_signed_url returned unexpected shape: {res!r}")
    return url


def download_to_tmp(path: str) -> Path:
    """
    Download a stored PDF to a named temporary file and return its Path.
    Caller is responsible for deleting the file when done (or letting the
    OS reclaim /tmp).
    """
    client = _require_client()
    data = client.storage.from_(BUCKET).download(path)
    if not data:
        raise RuntimeError(f"Empty download for path={path}")

    suffix = Path(path).suffix or ".pdf"
    fd, name = tempfile.mkstemp(prefix="jobmat_", suffix=suffix)
    try:
        with os.fdopen(fd, "wb") as f:
            f.write(data)
    except Exception:
        os.unlink(name)
        raise
    return Path(name)


def delete_object(path: str) -> None:
    """Delete a single storage object (no-op if missing)."""
    client = _require_client()
    try:
        client.storage.from_(BUCKET).remove([path])
        logger.info(f"Deleted {path} from bucket={BUCKET}")
    except Exception as e:
        logger.warning(f"Delete failed for {path}: {e}")


def delete_all_for_job(job_id: str | int) -> list[str]:
    """
    Delete every object under the `{job_id}/` prefix. Returns the list of
    paths that were attempted.
    """
    client = _require_client()
    prefix = f"{job_id}"
    try:
        listing = client.storage.from_(BUCKET).list(prefix) or []
    except Exception as e:
        logger.warning(f"List failed for prefix={prefix}: {e}")
        listing = []

    paths = [f"{prefix}/{entry['name']}" for entry in listing if entry.get("name")]
    # Belt-and-suspenders: always try the two canonical keys even if list
    # returned nothing (some SDKs skip deeply-nested listings).
    for kind in ("resume", "cover_letter"):
        p = _path_for(job_id, kind)
        if p not in paths:
            paths.append(p)

    if not paths:
        return []

    try:
        client.storage.from_(BUCKET).remove(paths)
        logger.info(f"Deleted {len(paths)} objects for job_id={job_id}: {paths}")
    except Exception as e:
        logger.warning(f"Bulk delete failed for job {job_id}: {e}")

    return paths
