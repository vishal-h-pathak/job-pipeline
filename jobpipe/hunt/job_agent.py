"""
job_agent.py — orchestration loop for the hunter.

Pipeline:
    1. Iterate every source. Each source already deduplicates within itself
       and now hashes job IDs source-agnostically (utils/jobid), so the same
       posting on Greenhouse + SerpAPI collapses to one row.
    2. Skip jobs already in Supabase (``get_seen_ids``).
    3. HEAD-validate the URL; drop dead links before spending Claude credits.
    4. Enrich descriptions that look like a marketing blurb.
    5. Score against ``CLAUDE.md`` via Claude.
    6. Upsert into Supabase. ``send_digest`` keeps the legacy email path alive.

Two operating modes (see ``config.py``):
    - ``local_remote`` (default): only Atlanta, GA + remote roles.
    - ``us_wide``: also pulls non-remote US roles. Useful for wide sweeps.

Examples:
    python3 job_agent.py                    # local_remote (default)
    python3 job_agent.py --mode us_wide
    HUNTER_MODE=us_wide python3 job_agent.py
"""

from __future__ import annotations

import argparse
import logging
import sys
import traceback

from dotenv import load_dotenv

load_dotenv()

import config  # noqa: E402  (must come after load_dotenv)
from sources import (  # noqa: E402
    remoteok,
    serpapi,
    greenhouse,
    lever,
    ashby,
    workday,
    hn_whoshiring,
    eighty_thousand_hours,
    jsearch,
)
# ``indeed`` and ``linkedin`` modules remain on disk for reference but are
# excluded from the active pipeline: Indeed RSS is fully gated and
# LinkedIn-via-SerpAPI returned 0 results across two runs. JSearch covers
# both of their job-publisher footprints behind one paid subscription.
# ``wellfound`` remains a stub (no public API).
from scorer import score_job, should_notify  # noqa: E402
from notifier import send_digest  # noqa: E402
from utils.validator import validate_url  # noqa: E402
from utils.enricher import enrich_description  # noqa: E402
from db import get_seen_ids, upsert_job  # noqa: E402

# ── Logging — stream to stdout so run_agent.sh's redirect captures it ─────
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s — %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
logger = logging.getLogger("job_agent")

# Order is intentional: cheap / free / direct-ATS sources run first so
# their results populate the cross-source dedup set before the paid
# SerpAPI / JSearch calls. That way a Greenhouse posting we already have
# doesn't spend a paid search just to be deduped after.
SOURCES = (
    greenhouse,             # free, curated ATS boards (J-1, portals.yml)
    lever,                  # free, curated ATS boards (J-1, portals.yml)
    ashby,                  # free, curated ATS boards (J-1, portals.yml)
    workday,                # free, curated ATS boards (J-1, portals.yml)
    hn_whoshiring,          # free, monthly HN thread
    eighty_thousand_hours,  # free, mission-driven
    remoteok,               # free, broad remote
    jsearch,                # paid (RapidAPI), Indeed + LinkedIn aggregator
    serpapi,                # paid (SerpAPI), Google Jobs main
)


def iter_all_jobs():
    """Iterate every source, yielding job dicts with cross-source dedup.

    Sources already dedupe internally (per-source ``seen_local`` sets), but
    two sources can surface the same role under different URLs — e.g. a
    Greenhouse posting also showing up via SerpAPI. We carry a process-wide
    ``seen_ids`` set keyed on the canonical job id (``utils.jobid``) so the
    second occurrence is skipped before scoring.
    """
    seen_ids: set[str] = set()
    for src in SOURCES:
        try:
            for job in src.fetch():
                if job["id"] in seen_ids:
                    logger.debug("cross-source dedup hit on %s (id=%s)",
                                 src.__name__, job["id"])
                    continue
                seen_ids.add(job["id"])
                yield job
        except Exception as e:
            logger.error("[%s] error: %s", src.__name__, e)
            traceback.print_exc()


def run() -> None:
    mode = config.get_mode()
    logger.info("hunter run starting (mode=%s)", mode)

    seen = get_seen_ids()
    new_count = 0
    skipped_dead = 0
    enriched_count = 0
    to_notify: list[dict] = []
    by_source: dict[str, int] = {}

    for job in iter_all_jobs():
        if job["id"] in seen:
            continue
        new_count += 1
        by_source[job.get("source", "unknown")] = (
            by_source.get(job.get("source", "unknown"), 0) + 1
        )

        # ── Pre-validate URL before spending API credits on scoring ──
        if not validate_url(job["url"]):
            logger.info("[validator] dead link, skipping before score: %s", job["url"])
            skipped_dead += 1
            continue

        # ── Enrich sparse descriptions ───────────────────────────────
        original_len = len(job.get("description", ""))
        job = enrich_description(job)
        if len(job.get("description", "")) > original_len:
            enriched_count += 1

        # ── Score ────────────────────────────────────────────────────
        try:
            result = score_job(
                title=job["title"],
                company=job["company"],
                description=job["description"],
                location=job["location"],
            )
        except Exception as e:
            logger.error("[scorer] error on %r: %s", job["title"], e)
            continue

        if should_notify(result):
            to_notify.append({"job": job, "score": result})

        try:
            upsert_job(job, result)
            seen.add(job["id"])
        except Exception as e:
            logger.error("[db] upsert error for %s: %s", job["id"], e)

    if to_notify:
        send_digest(to_notify)

    logger.info(
        "done. mode=%s new=%d enriched=%d dead_skipped=%d notified=%d by_source=%s",
        mode, new_count, enriched_count, skipped_dead, len(to_notify), by_source,
    )
    print(f"done. mode={mode} new jobs: {new_count}, enriched: {enriched_count}, "
          f"dead links skipped: {skipped_dead}, notified: {len(to_notify)}")


def main() -> None:
    parser = argparse.ArgumentParser(description="job-hunter orchestration loop")
    parser.add_argument(
        "--mode",
        choices=("local_remote", "us_wide"),
        default=None,
        help="Search scope. local_remote = Atlanta + Remote (default); "
             "us_wide adds national US roles. Falls back to HUNTER_MODE env "
             "var, then 'local_remote'.",
    )
    args = parser.parse_args()
    if args.mode:
        config.set_mode(args.mode)
    run()


if __name__ == "__main__":
    main()
