"""Rule S5: spend is capped, and the cap is computed from real usage."""

from __future__ import annotations

import pytest

from walker.budget import (
    BudgetExceeded, BudgetGuard, Prices, Usage, cost_usd, estimate_session_cost, load_pricing,
)

PRICES = Prices(input=0.10, output=0.50, cache_write=0.125, cache_read=0.01)


def test_cost_uses_all_four_prices() -> None:
    usage = Usage(input_tokens=1_000_000, output_tokens=1_000_000,
                  cache_creation_input_tokens=1_000_000, cache_read_input_tokens=1_000_000)
    assert cost_usd(usage, PRICES) == pytest.approx(0.10 + 0.50 + 0.125 + 0.01)


def test_a_typical_step_costs_a_fraction_of_a_cent() -> None:
    step = cost_usd(Usage(input_tokens=1200, output_tokens=60), PRICES)
    assert step == pytest.approx(0.00015, rel=1e-6)
    session = estimate_session_cost(PRICES)
    assert 0.002 < session < 0.004  # about $0.003 for a 17-step session


def test_usage_is_read_off_the_response_object() -> None:
    class SDKUsage:
        input_tokens = 1200
        output_tokens = 60
        cache_creation_input_tokens = 500
        cache_read_input_tokens = 700

    usage = Usage.from_response(SDKUsage())
    assert (usage.input_tokens, usage.output_tokens) == (1200, 60)
    assert usage.cache_creation_input_tokens == 500
    assert usage.cache_read_input_tokens == 700
    assert usage.billed_input == 2400


def test_missing_usage_fields_are_treated_as_zero_not_crashes() -> None:
    class Sparse:
        input_tokens = 100
        output_tokens = None

    usage = Usage.from_response(Sparse())
    assert usage.input_tokens == 100
    assert usage.output_tokens == 0
    assert usage.cache_read_input_tokens == 0

    assert Usage.from_response({"input_tokens": 5}).input_tokens == 5


def test_guard_refuses_a_new_session_once_the_cap_is_reached() -> None:
    guard = BudgetGuard(PRICES, cap_usd=0.001, per_session_cap_usd=0.02)
    guard.check_can_start_session()  # fine while nothing is spent

    guard.record(Usage(input_tokens=1_000_000, output_tokens=0), session_id="s1")
    assert guard.spent_usd == pytest.approx(0.10)
    with pytest.raises(BudgetExceeded, match="cap reached"):
        guard.check_can_start_session()


def test_guard_counts_spend_already_in_the_database() -> None:
    guard = BudgetGuard(PRICES, cap_usd=10.0, per_session_cap_usd=0.02,
                        already_spent_usd=9.999)
    assert guard.remaining_usd == pytest.approx(0.001)
    with pytest.raises(BudgetExceeded):
        guard.check_estimate(0.5)


def test_plan_refuses_when_the_estimate_exceeds_what_is_left() -> None:
    guard = BudgetGuard(PRICES, cap_usd=10.0, per_session_cap_usd=0.02, already_spent_usd=9.0)
    guard.check_estimate(0.5)  # fits
    with pytest.raises(BudgetExceeded, match="exceeds remaining"):
        guard.check_estimate(2.0)


def test_per_session_cap_is_tracked_separately() -> None:
    guard = BudgetGuard(PRICES, cap_usd=10.0, per_session_cap_usd=0.0002)
    assert not guard.session_over_cap("s1")
    guard.record(Usage(input_tokens=1200, output_tokens=60), session_id="s1")
    assert guard.session_over_cap("s1")
    assert not guard.session_over_cap("s2")
    assert guard.session_cost_usd("s1") == pytest.approx(0.00015)


def test_spend_is_reported_to_the_callback_for_the_spend_table() -> None:
    recorded: list[dict] = []
    guard = BudgetGuard(PRICES, cap_usd=10.0, per_session_cap_usd=0.02,
                        on_spend=recorded.append)
    guard.record(Usage(input_tokens=1200, output_tokens=60, cache_read_input_tokens=800),
                 session_id="s1", model="claude-haiku-5-5", run_id="r1")
    assert len(recorded) == 1
    entry = recorded[0]
    assert entry["session_id"] == "s1"
    assert entry["run_id"] == "r1"
    assert entry["model"] == "claude-haiku-5-5"
    assert entry["cache_read_input_tokens"] == 800
    assert entry["usd"] > 0


def test_pricing_config_matches_the_published_prices() -> None:
    pricing = load_pricing("configs/pricing.yaml")
    assert pricing.model == "claude-haiku-5-5"
    assert pricing.prices.input == 0.10
    assert pricing.prices.output == 0.50
    assert pricing.prices.cache_write == 0.125
    assert pricing.prices.cache_read == 0.01
    assert pricing.caps.run == 10.00
    assert pricing.caps.development == 5.00
    assert pricing.caps.per_session == 0.02
    assert pricing.limits.max_steps_per_session == 30
    assert pricing.default_requests_per_minute == 40


def test_the_whole_planned_demo_fits_well_inside_the_cap() -> None:
    """Roughly 500 sessions across pilot, ON, OFF and the live demo."""
    pricing = load_pricing("configs/pricing.yaml")
    sessions = 20 + 180 + 120 + 2
    total = sessions * estimate_session_cost(pricing.prices)
    assert total < pricing.caps.run / 5, f"${total:.4f} is more headroom than expected"
