"""jobpipe/hunt/rescore.py — re-score existing jobs rows against the current thesis.

Session G (feat/hunt-thesis): the scorer's context changed materially
(thesis.md, tier 1.5, degree gate), so historical rows carry stale
scores. ``jobpipe-hunt --rescore`` replays score_job over existing rows
and writes the fresh score / tier / degree_gated back, stamping
``rescored_at``.

Safety model:
- DRY-RUN BY DEFAULT. Without ``--execute`` it prints the row count and
  an itemized LLM cost estimate, then exits. Nothing is written and no
  Anthropic call is made.
- Only rows in passive statuses are eligible. ``--status`` is
  restricted to RESCORABLE_STATUSES; anything from ``approved`` onward
  (in-flight or terminal pipeline work) is never touched. The UPDATE
  also re-asserts the row's status optimistically, so a row approved
  mid-run is skipped rather than clobbered.

Cost estimate: token counts are chars/4 heuristics over the *actual*
system prompt, profile context, and row descriptions, so the estimate
tracks the real prompt sizes as thesis.md / portals grow. Prices are
USD per million tokens for the scorer model and can be overridden via
RESCORE_INPUT_USD_PER_MTOK / RESCORE_OUTPUT_USD_PER_MTOK when pricing
changes.
"""

from __future__ import annotations

import logging
import math
import os
import time
from datetime import datetime, timedelta, timezone

logger = logging.getLogger("hunt.rescore")

# Statuses the rescore is allowed to read & rewrite. Everything else —
# approved onward — belongs to the tailor/submit lifecycle and is
# off-limits (M-2 status flow).
RESCORABLE_STATUSES = ("new", "discovered", "ignored")

# Scorer model pricing, USD per million tokens. Matches jobpipe.hunt
# .scorer.MODEL ("claude-sonnet-4-6" as of the 2026-06 cost-control switch
# from Opus 4.7). Override via env when pricing or the model changes.
INPUT_USD_PER_MTOK = float(os.environ.get("RESCORE_INPUT_USD_PER_MTOK", "3.0"))
OUTPUT_USD_PER_MTOK = float(os.environ.get("RESCORE_OUTPUT_USD_PER_MTOK", "15.0"))

# Observed scorer responses are a small JSON object (max_tokens=600).
EST_OUTPUT_TOKENS_PER_ROW = 250

DEFAULT_BATCH_SIZE = 25


def _est_tokens(chars: int) -> int:
    """chars/4 heuristic — good enough for a budget estimate."""
    return math.ceil(chars / 4)


def fetch_rescorable(status: str = "new", since_days: int | None = None) -> list[dict]:
    """Rows eligible for re-scoring, oldest first.

    ``status`` must be one of RESCORABLE_STATUSES — guarded here as well
    as at the CLI so a future caller can't point this at approved rows.
    """
    if status not in RESCORABLE_STATUSES:
        raise ValueError(
            f"status {status!r} is not rescorable; allowed: {RESCORABLE_STATUSES}"
        )
    import jobpipe.db as db

    # Service-role client: the 2026-06-12 enable_rls_dashboard_tables
    # migration turned on RLS for `jobs` with no anon policies, so the
    # anon client reads back empty. Rescore is an operator tool run from
    # a trusted environment — same pattern as the dashboard's
    # server-side routes.
    q = (
        db.service_client.table("jobs")
        .select("id,title,company,location,description,status,created_at")
        .eq("status", status)
        .order("created_at", desc=False)
    )
    if since_days is not None:
        cutoff = (datetime.now(timezone.utc) - timedelta(days=since_days)).isoformat()
        q = q.gte("created_at", cutoff)
    rows = q.execute().data or []
    # Belt-and-braces: drop anything whose status doesn't match the ask.
    return [r for r in rows if (r.get("status") or "new") == status]


def estimate_cost(rows: list[dict]) -> dict:
    """Itemized LLM cost estimate for re-scoring ``rows``.

    Builds the real system prompt + profile context so fixed-prompt
    growth (e.g. a longer thesis) is reflected without hand-tuning.
    """
    from jobpipe.hunt.prompts import build_profile_prompt_string, load_prompt

    system_tokens = _est_tokens(len(load_prompt("scorer")))
    profile_tokens = _est_tokens(len(build_profile_prompt_string()))
    per_row_overhead = 60  # title/company/location + message framing

    desc_tokens_total = sum(
        _est_tokens(len(r.get("description") or "")) for r in rows
    )
    n = len(rows)
    input_tokens = n * (system_tokens + profile_tokens + per_row_overhead) + desc_tokens_total
    output_tokens = n * EST_OUTPUT_TOKENS_PER_ROW
    cost = (
        input_tokens / 1_000_000 * INPUT_USD_PER_MTOK
        + output_tokens / 1_000_000 * OUTPUT_USD_PER_MTOK
    )
    return {
        "rows": n,
        "system_tokens_per_row": system_tokens,
        "profile_tokens_per_row": profile_tokens,
        "input_tokens": input_tokens,
        "output_tokens": output_tokens,
        "input_usd_per_mtok": INPUT_USD_PER_MTOK,
        "output_usd_per_mtok": OUTPUT_USD_PER_MTOK,
        "estimated_usd": round(cost, 2),
    }


def _write_rescored(job_id: str, expected_status: str, result: dict) -> bool:
    """Write fresh score fields for one row.

    The update re-asserts ``status == expected_status`` so a row that
    moved into the active pipeline between fetch and write is left
    alone. Returns True if a row was updated.
    """
    import jobpipe.db as db

    resp = (
        db.service_client.table("jobs")  # RLS: see fetch_rescorable
        .update(
            {
                "score": result.get("score"),
                "tier": result.get("tier"),
                "degree_gated": bool(result.get("degree_gated", False)),
                "reasoning": result.get("reasoning"),
                "action": result.get("recommended_action"),
                "legitimacy": result.get("legitimacy"),
                "legitimacy_reasoning": result.get("legitimacy_reasoning"),
                "rescored_at": datetime.now(timezone.utc).isoformat(),
            }
        )
        .eq("id", job_id)
        .eq("status", expected_status)
        .execute()
    )
    return bool(resp.data)


def run_rescore(
    rows: list[dict],
    batch_size: int = DEFAULT_BATCH_SIZE,
    sleep_between_batches: float = 0.0,
) -> dict:
    """Re-score ``rows`` in batches with progress logging."""
    from jobpipe.hunt.scorer import score_job

    stats = {"scored": 0, "written": 0, "skipped_status_changed": 0, "errors": 0}
    total = len(rows)
    n_batches = math.ceil(total / batch_size) if total else 0
    for b in range(n_batches):
        batch = rows[b * batch_size : (b + 1) * batch_size]
        for row in batch:
            try:
                result = score_job(
                    title=row.get("title") or "",
                    company=row.get("company") or "",
                    description=row.get("description") or "",
                    location=row.get("location") or "",
                )
                stats["scored"] += 1
            except Exception as e:  # keep going; one bad row shouldn't kill the run
                stats["errors"] += 1
                logger.error("[rescore] scorer error on %s: %s", row.get("id"), e)
                continue
            try:
                if _write_rescored(row["id"], row.get("status") or "new", result):
                    stats["written"] += 1
                else:
                    stats["skipped_status_changed"] += 1
            except Exception as e:
                stats["errors"] += 1
                logger.error("[rescore] db write error on %s: %s", row.get("id"), e)
        done = min((b + 1) * batch_size, total)
        logger.info(
            "[rescore] batch %d/%d done — %d/%d rows (written=%d, "
            "status-changed=%d, errors=%d)",
            b + 1, n_batches, done, total,
            stats["written"], stats["skipped_status_changed"], stats["errors"],
        )
        print(f"[rescore] {done}/{total} rows processed")
        if sleep_between_batches and b + 1 < n_batches:
            time.sleep(sleep_between_batches)
    return stats


def main(status: str, since_days: int | None, execute: bool,
         batch_size: int = DEFAULT_BATCH_SIZE) -> None:
    """Entry point for ``jobpipe-hunt --rescore``."""
    rows = fetch_rescorable(status=status, since_days=since_days)
    est = estimate_cost(rows)
    print(
        f"[rescore] eligible rows: {est['rows']} (status={status}"
        + (f", since={since_days}d" if since_days is not None else "")
        + ")"
    )
    print(
        f"[rescore] estimated cost: ${est['estimated_usd']:.2f} "
        f"({est['input_tokens']:,} input + {est['output_tokens']:,} output tokens "
        f"@ ${est['input_usd_per_mtok']}/M in, ${est['output_usd_per_mtok']}/M out; "
        f"~{est['system_tokens_per_row'] + est['profile_tokens_per_row']:,} "
        f"fixed prompt tokens per row)"
    )
    if not execute:
        print("[rescore] dry-run (default): nothing scored, nothing written. "
              "Re-run with --execute to spend the tokens.")
        return
    stats = run_rescore(rows, batch_size=batch_size)
    print(
        f"[rescore] done. scored={stats['scored']} written={stats['written']} "
        f"skipped_status_changed={stats['skipped_status_changed']} "
        f"errors={stats['errors']}"
    )
