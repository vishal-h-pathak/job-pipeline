"""jobpipe.shared.cost — guarded cost-event recorder + attribution context.

The write side of the cost tracker. Call sites (the hunt scorer, the five
tailor modules, interview-prep, the submit prepare-loop, the external API
counters) hand this module a model + usage, and it writes one row to
``public.cost_events`` with the dollar cost from :mod:`jobpipe.shared.pricing`.

Two design rules from the build plan, both load-bearing:

  1. **Attribution rides a contextvar, not call args.** A CLI opens a
     ``with cost_context(run_id=..., stage="hunt")`` at run start; code
     deep in the stack records without threading ``run_id``/``stage``/
     ``job_id`` through every signature. Contexts nest and merge, so a
     per-job ``with cost_context(job_id=...)`` adds to the surrounding
     stage context and pops cleanly.

  2. **Every write is best-effort.** Telemetry must NEVER fail or slow the
     pipeline, so each DB call is wrapped in ``try/except`` that logs and
     swallows. A Supabase blip costs us a missing cost row, nothing more.

Dollars are attributed only to the API-key path: ``auth_path="oauth"``
records token counts at ``cost_usd=0`` so subscription calls stay visible
without being double-counted against the API budget.

Writes go through ``jobpipe.db.service_client`` (the canonical
service-role client) — the pipeline tables are RLS-without-policies, so an
anon client would silently no-op.
"""

from __future__ import annotations

import contextlib
import contextvars
import logging
from typing import Any, Iterator

from jobpipe.shared import pricing

logger = logging.getLogger("jobpipe.shared.cost")

# Current attribution: {run_id, stage, job_id}. Empty until a CLI opens a
# cost_context. Default is a fresh empty dict (never mutated in place —
# cost_context always sets a new merged dict and resets the token).
_COST_CONTEXT: contextvars.ContextVar[dict[str, Any]] = contextvars.ContextVar(
    "jobpipe_cost_context", default={}
)


def _current() -> dict[str, Any]:
    """Return the current attribution dict (possibly empty)."""
    return _COST_CONTEXT.get()


@contextlib.contextmanager
def cost_context(**kwargs: Any) -> Iterator[None]:
    """Merge ``kwargs`` (``run_id`` / ``stage`` / ``job_id``) into the
    attribution context for the duration of the block, then restore.

    Nests: an inner context inherits the outer keys and adds its own; on
    exit the outer context is restored exactly (the contextvar token is
    reset), so a per-job ``job_id`` doesn't leak past its block.
    """
    merged = {**_COST_CONTEXT.get(), **kwargs}
    token = _COST_CONTEXT.set(merged)
    try:
        yield
    finally:
        _COST_CONTEXT.reset(token)


def _usage_get(usage: Any, key: str) -> int:
    """Read ``key`` from an Anthropic usage object or a plain dict.

    Tolerates missing / ``None`` fields (e.g. the cache_* fields are absent
    on calls that don't touch the prompt cache) by treating them as 0.
    """
    if isinstance(usage, dict):
        value = usage.get(key)
    else:
        value = getattr(usage, key, None)
    return int(value or 0)


def _service_client():
    """Return the canonical service-role Supabase client (lazy)."""
    import jobpipe.db as db
    return db.service_client


def _insert_event(
    *,
    service: str,
    stage: str | None,
    model: str | None,
    auth_path: str | None,
    units: dict[str, Any],
    cost_usd: float,
) -> None:
    """Insert one cost_events row, pulling run_id/job_id/stage from context.

    Best-effort: any failure is logged and swallowed so a telemetry write
    can never break a billable call site.
    """
    ctx = _current()
    row = {
        "run_id": ctx.get("run_id"),
        "stage": stage if stage is not None else ctx.get("stage", "unknown"),
        "service": service,
        "model": model,
        "auth_path": auth_path,
        "job_id": ctx.get("job_id"),
        "units": units,
        "cost_usd": cost_usd,
    }
    try:
        _service_client().table("cost_events").insert(row).execute()
    except Exception as exc:  # noqa: BLE001 — telemetry must never raise
        logger.warning("cost_events insert failed (%s): %s", service, exc)


def record_anthropic(model: str, usage: Any, auth_path: str) -> None:
    """Record one Anthropic call from its ``usage`` object.

    Builds ``units`` from the four token fields, prices them via
    :func:`pricing.anthropic_cost_usd`, and writes a ``cost_events`` row.
    ``auth_path="oauth"`` forces ``cost_usd=0`` (subscription call) while
    still recording the token counts so the call stays visible.
    """
    input_tokens = _usage_get(usage, "input_tokens")
    output_tokens = _usage_get(usage, "output_tokens")
    cache_read = _usage_get(usage, "cache_read_input_tokens")
    cache_creation = _usage_get(usage, "cache_creation_input_tokens")

    units = {
        "input_tokens": input_tokens,
        "output_tokens": output_tokens,
        "cache_read": cache_read,
        "cache_creation": cache_creation,
    }

    if auth_path == "oauth":
        cost_usd = 0.0
    else:
        cost_usd = pricing.anthropic_cost_usd(
            model,
            input_tokens,
            output_tokens,
            cache_read=cache_read,
            cache_creation=cache_creation,
        )

    _insert_event(
        service="anthropic",
        stage=None,
        model=model,
        auth_path=auth_path,
        units=units,
        cost_usd=cost_usd,
    )


def record_units(
    service: str,
    units: dict[str, Any],
    cost_usd: float,
    stage: str | None = None,
) -> None:
    """Record a generic non-token billable event (SerpAPI, JSearch, etc.).

    ``stage`` overrides the contextual stage when a counter fires outside
    the stage that owns it.
    """
    _insert_event(
        service=service,
        stage=stage,
        model=None,
        auth_path=None,
        units=units,
        cost_usd=cost_usd,
    )


def rollup_run(run_id: str) -> None:
    """Sum a run's ``cost_events.cost_usd`` into ``runs.cost_usd``.

    Best-effort end-of-run finalize step. A failure leaves the denormalized
    rollup stale but the per-event ledger intact, so all-time totals stay
    correct.
    """
    try:
        client = _service_client()
        rows = (
            client.table("cost_events")
            .select("cost_usd")
            .eq("run_id", run_id)
            .execute()
            .data
            or []
        )
        total = round(sum(float(r.get("cost_usd") or 0) for r in rows), 4)
        client.table("runs").update({"cost_usd": total}).eq("id", run_id).execute()
    except Exception as exc:  # noqa: BLE001 — telemetry must never raise
        logger.warning("rollup_run failed for %s: %s", run_id, exc)
