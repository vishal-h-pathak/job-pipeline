"""interview_prep/bank.py — Read/write/search the star_stories table (J-3).

Thin Supabase wrapper. The dashboard does its own client-side reads
through the anon key + RLS-friendly schema, but the agent writes via
this module using the service role.
"""

from __future__ import annotations

import logging
from typing import Iterable, Optional

from jobpipe.db import client

logger = logging.getLogger("interview_prep.bank")


def save_stories(
    job_id: Optional[str],
    archetype: Optional[str],
    company: Optional[str],
    role: Optional[str],
    stories: Iterable[dict],
) -> int:
    """Insert one or more stories. Returns the count actually written."""
    rows = []
    for s in stories:
        rows.append(
            {
                "job_id": job_id,
                "archetype": archetype,
                "company": company,
                "role": role,
                "situation": s["situation"],
                "task": s["task"],
                "action": s["action"],
                "result": s["result"],
                "reflection": s["reflection"],
                "tags": s.get("tags") or [],
            }
        )
    if not rows:
        return 0
    try:
        client.table("star_stories").insert(rows).execute()
    except Exception as exc:
        logger.warning("Saving %d STAR+R stories failed: %s", len(rows), exc)
        return 0
    logger.info("Saved %d STAR+R stories (job=%s, archetype=%s)", len(rows), job_id, archetype)
    return len(rows)


def list_stories(
    archetype: Optional[str] = None,
    tag: Optional[str] = None,
    master_only: bool = False,
    limit: int = 200,
) -> list[dict]:
    """Fetch stories with optional filters. Used by the dashboard
    /api routes; the dashboard mostly reads directly with the anon key.
    """
    q = client.table("star_stories").select("*")
    if archetype:
        q = q.eq("archetype", archetype)
    if tag:
        q = q.contains("tags", [tag])
    if master_only:
        q = q.eq("is_master", True)
    q = q.order("created_at", desc=True).limit(limit)
    return q.execute().data or []


def set_master(story_id: int, is_master: bool) -> None:
    """Toggle whether a story is part of the master set."""
    client.table("star_stories").update({"is_master": is_master}).eq("id", story_id).execute()
