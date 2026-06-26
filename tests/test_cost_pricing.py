"""Unit tests for jobpipe.shared.pricing — token → USD conversion.

Covers the rate-table math (known model families), the cache-read 90%
discount, cache-creation at full input rate, and the never-raise
contract for an unknown model (cost 0 + a logged warning).
"""

from __future__ import annotations

import logging

import pytest

from jobpipe.shared import pricing


# ── Rate table sanity ────────────────────────────────────────────────────

def test_rates_cover_the_three_families():
    assert set(pricing.RATES) == {
        "claude-opus-4",
        "claude-sonnet-4",
        "claude-haiku-4",
    }
    for fam, rate in pricing.RATES.items():
        assert rate["in"] > 0 and rate["out"] > 0, fam


def test_external_service_constants():
    assert pricing.SERPAPI_USD_PER_SEARCH == 0.0
    assert pricing.JSEARCH_USD_PER_REQUEST == pytest.approx(10 / 1500)


# ── anthropic_cost_usd: known-model math ─────────────────────────────────

def test_opus_input_output_math():
    # Opus: $5 / Mtok in, $25 / Mtok out. A full Mtok each → $5 + $25.
    cost = pricing.anthropic_cost_usd("claude-opus-4-8", 1_000_000, 1_000_000)
    assert cost == pytest.approx(30.0)


def test_sonnet_prefix_match_on_dated_id():
    # claude-sonnet-4-6 must match the claude-sonnet-4 family by prefix.
    cost = pricing.anthropic_cost_usd("claude-sonnet-4-6", 1_000_000, 0)
    assert cost == pytest.approx(3.0)


def test_haiku_output_only():
    # Haiku: $5 / Mtok out.
    cost = pricing.anthropic_cost_usd("claude-haiku-4-5", 0, 2_000_000)
    assert cost == pytest.approx(10.0)


def test_cache_read_is_ten_percent_of_input_rate():
    # cache_read priced at -90% of input rate → 10% of $5 = $0.50 per Mtok.
    cost = pricing.anthropic_cost_usd(
        "claude-opus-4-8", 0, 0, cache_read=1_000_000
    )
    assert cost == pytest.approx(0.5)


def test_cache_creation_is_full_input_rate():
    # cache_creation priced at the input rate → $5 per Mtok for Opus.
    cost = pricing.anthropic_cost_usd(
        "claude-opus-4-8", 0, 0, cache_creation=1_000_000
    )
    assert cost == pytest.approx(5.0)


def test_all_components_sum():
    # in + out + cache_read(10%) + cache_creation(100%), Sonnet rates.
    cost = pricing.anthropic_cost_usd(
        "claude-sonnet-4-6",
        input_tokens=1_000_000,     # $3
        output_tokens=1_000_000,    # $15
        cache_read=1_000_000,       # $0.30
        cache_creation=1_000_000,   # $3
    )
    assert cost == pytest.approx(3.0 + 15.0 + 0.30 + 3.0)


def test_zero_tokens_zero_cost():
    assert pricing.anthropic_cost_usd("claude-opus-4-8", 0, 0) == 0.0


# ── Unknown model: never raise, cost 0, warn ─────────────────────────────

def test_unknown_model_returns_zero_and_warns(caplog):
    with caplog.at_level(logging.WARNING, logger="jobpipe.shared.pricing"):
        cost = pricing.anthropic_cost_usd("gpt-4o", 1_000_000, 1_000_000)
    assert cost == 0.0
    assert any("gpt-4o" in r.message for r in caplog.records)


def test_unknown_model_does_not_raise():
    # Explicit: a surprise model id must never blow up a billable call site.
    assert pricing.anthropic_cost_usd("some-future-model", 999, 999) == 0.0
