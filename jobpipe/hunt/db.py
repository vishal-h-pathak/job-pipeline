import os
from datetime import datetime, timezone

from supabase import create_client, Client


def _client() -> Client:
    url = os.environ["SUPABASE_URL"]
    key = os.environ["SUPABASE_KEY"]
    return create_client(url, key)


def upsert_job(job: dict, result: dict) -> None:
    client = _client()
    existing = (
        client.table("jobs").select("id").eq("id", job["id"]).execute().data or []
    )
    if existing:
        client.table("jobs").update(
            {
                "score": result.get("score"),
                "tier": result.get("tier"),
                "reasoning": result.get("reasoning"),
                "action": result.get("recommended_action"),
            }
        ).eq("id", job["id"]).execute()
    else:
        # Belt-and-suspenders: explicitly set created_at + status so rows are
        # well-formed even if a DB default is missing or gets dropped.
        now_iso = datetime.now(timezone.utc).isoformat()
        client.table("jobs").upsert(
            {
                "id": job["id"],
                "title": job.get("title"),
                "company": job.get("company"),
                "location": job.get("location"),
                "description": job.get("description"),
                "url": job.get("url"),
                "source": job.get("source"),
                "score": result.get("score"),
                "tier": result.get("tier"),
                "reasoning": result.get("reasoning"),
                "action": result.get("recommended_action"),
                "status": "new",
                "created_at": now_iso,
            },
            on_conflict="id",
        ).execute()


def get_seen_ids() -> set[str]:
    rows = _client().table("jobs").select("id").execute().data or []
    return {r["id"] for r in rows}
