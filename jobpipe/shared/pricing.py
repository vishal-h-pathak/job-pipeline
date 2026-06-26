"""jobpipe.shared.pricing — token → USD rate table for the cost tracker.

A tiny, dependency-free rate table plus one helper. Kept deliberately
small and pure so every billable call site (S2–S5) can convert token
counts to dollars without reaching for the network or raising.

Rates are USD per million tokens, by model *family* — a call's model id
(e.g. ``claude-sonnet-4-6``) is matched to a family by prefix so dated
point releases keep pricing correctly without a table edit. The numbers
mirror Anthropic's published list pricing as of the build plan
(2026-06-26); they are duplicated in the portfolio's TypeScript rate
table (S4) — keep the two in sync.

Cache accounting follows Anthropic's billing model:
  - ``cache_read`` (prompt-cache hit) is billed at -90% of the input
    rate (i.e. 10% of input).
  - ``cache_creation`` (writing a cache block) is billed at the input
    rate.

An unknown model never raises and never guesses: it returns ``0.0`` and
logs a warning, so a surprise model id degrades telemetry rather than
breaking the pipeline.
"""

from __future__ import annotations

import logging

logger = logging.getLogger("jobpipe.shared.pricing")

# USD per million tokens, by model family. cache_read priced at -90% of
# input; cache_creation at the input rate (see module docstring).
RATES: dict[str, dict[str, float]] = {
    "claude-opus-4":   {"in": 5.0, "out": 25.0},
    "claude-sonnet-4": {"in": 3.0, "out": 15.0},
    "claude-haiku-4":  {"in": 1.0, "out": 5.0},
}

SERPAPI_USD_PER_SEARCH = 0.0       # free tier today; informational count
JSEARCH_USD_PER_REQUEST = 10 / 1500  # Basic plan ~$10 / 1,500 requests

# Fraction of the input rate charged for a prompt-cache read (a 90% discount).
_CACHE_READ_DISCOUNT = 0.1

_PER_MTOK = 1_000_000


def _family_for(model: str) -> dict[str, float] | None:
    """Return the rate dict whose family is a prefix of ``model``.

    Longest-prefix wins so a hypothetical ``claude-opus-4`` vs a future
    ``claude-opus-40`` can't collide on the shorter key.
    """
    matches = [
        (family, rate)
        for family, rate in RATES.items()
        if model.startswith(family)
    ]
    if not matches:
        return None
    matches.sort(key=lambda kv: len(kv[0]), reverse=True)
    return matches[0][1]


def anthropic_cost_usd(
    model: str,
    input_tokens: int,
    output_tokens: int,
    cache_read: int = 0,
    cache_creation: int = 0,
) -> float:
    """Convert Anthropic token counts to USD for ``model``.

    Matches ``model`` to a family in :data:`RATES` by prefix. ``cache_read``
    is priced at 10% of the input rate; ``cache_creation`` at the full
    input rate. An unknown model returns ``0.0`` and logs a warning — it
    never raises.
    """
    rate = _family_for(model)
    if rate is None:
        logger.warning(
            "no pricing for model %r — recording cost_usd=0", model
        )
        return 0.0

    in_rate = rate["in"]
    out_rate = rate["out"]
    return (
        input_tokens * in_rate
        + output_tokens * out_rate
        + cache_read * in_rate * _CACHE_READ_DISCOUNT
        + cache_creation * in_rate
    ) / _PER_MTOK
