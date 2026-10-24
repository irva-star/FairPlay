"""Game rules: seeded determinism and the planted probabilities.

The planted rule is the ground truth the whole engine is scored against, so it
is checked by simulation rather than taken on trust.
"""

from __future__ import annotations

import statistics
from collections import Counter

import pytest

from game import rules as R

N = 5_000


def every_level(count: int = 600):
    for seed in range(count):
        for level in range(1, R.LEVELS_TOTAL + 1):
            yield seed, level


# --- determinism --------------------------------------------------------------

def test_the_same_seed_gives_the_same_game() -> None:
    first = [R.base_chances(42, level) for level in range(1, 11)]
    second = [R.base_chances(42, level) for level in range(1, 11)]
    assert first == second


def test_different_seeds_give_different_games() -> None:
    games = {tuple(R.base_chances(seed, 1)) for seed in range(50)}
    assert len(games) > 40, "seeds should not collide this often"


def test_rolls_do_not_depend_on_the_path_taken() -> None:
    """A level's rolls must be identical however the player got there."""
    for seed, level in every_level(100):
        assert R.planted_rolls(seed, level) == R.planted_rolls(seed, level)


def test_the_three_streams_are_independent() -> None:
    doors = [R.base_chances(s, 1)[0] for s in range(500)]
    planted = [R.planted_rolls(s, 1).penalty for s in range(500)]
    assert statistics.fmean(planted) == pytest.approx(0.5, abs=0.05)
    assert len(set(doors)) > 10


# --- door chances -------------------------------------------------------------

def test_chances_stay_in_the_specified_ranges() -> None:
    for seed, level in every_level():
        chances = R.base_chances(seed, level)
        assert len(chances) == 3
        best = max(chances)
        assert R.BEST_CHANCE_MIN <= best <= R.BEST_CHANCE_MAX
        for chance in chances:
            if chance != best:
                assert R.OTHER_GAP_MIN <= best - chance <= R.OTHER_GAP_MAX
            assert 0 <= chance <= 100


def test_the_best_door_moves_around() -> None:
    positions = Counter(R.best_door(R.base_chances(s, 1)) for s in range(600))
    assert set(positions) == {0, 1, 2}
    for count in positions.values():
        assert count > 100, f"best door position is lopsided: {positions}"


# --- the planted rule ---------------------------------------------------------

def test_the_rule_only_fires_for_spenders_with_the_switch_on() -> None:
    assert R.is_targeted(switch_on=True, is_spender=True)
    assert not R.is_targeted(switch_on=True, is_spender=False)
    assert not R.is_targeted(switch_on=False, is_spender=True)
    assert not R.is_targeted(switch_on=False, is_spender=False)


def test_penalty_fires_about_seventy_percent_of_the_time_after_a_loss() -> None:
    hits = sum(R.penalty_applied(s, l, targeted=True, follows_loss=True)
               for s, l in every_level())
    total = sum(1 for _ in every_level())
    assert hits / total == pytest.approx(R.PENALTY_PROB, abs=0.02)


def test_penalty_never_fires_for_the_untargeted_or_without_a_loss() -> None:
    for seed, level in every_level(200):
        assert not R.penalty_applied(seed, level, targeted=False, follows_loss=True)
        assert not R.penalty_applied(seed, level, targeted=True, follows_loss=False)


def test_the_penalty_lowers_all_three_doors_by_fifteen_points() -> None:
    base = [60, 40, 45]
    assert R.shown_chances(base, penalty=True) == [45, 25, 30]
    assert R.shown_chances(base, penalty=False) == base


def test_the_penalty_can_never_push_a_chance_out_of_range() -> None:
    for seed, level in every_level(300):
        shown = R.shown_chances(R.base_chances(seed, level), penalty=True)
        assert all(0 <= c <= 100 for c in shown)


def test_offer_probabilities_match_the_spec() -> None:
    targeted = sum(R.offer_shown(s, l, targeted=True, lost=True) for s, l in every_level())
    base = sum(R.offer_shown(s, l, targeted=False, lost=True) for s, l in every_level())
    total = sum(1 for _ in every_level())
    assert targeted / total == pytest.approx(R.OFFER_PROB_TARGETED, abs=0.02)
    assert base / total == pytest.approx(R.OFFER_PROB_BASE, abs=0.02)
    assert R.offer_probability(targeted=True) == 0.8
    assert R.offer_probability(targeted=False) == 0.3


def test_no_offer_without_a_loss() -> None:
    for seed, level in every_level(200):
        assert not R.offer_shown(seed, level, targeted=True, lost=False)


def test_offer_price_is_a_negative_control() -> None:
    """Price must not differ by segment: it is the metric that proves the
    detector is not simply flagging everything."""
    prices = Counter(R.offer_price(s, l) for s, l in every_level())
    assert set(prices) == set(R.OFFER_PRICES)
    expected = sum(prices.values()) / 3
    for price, count in prices.items():
        assert count == pytest.approx(expected, rel=0.08), prices


def test_resolution_follows_the_chance_actually_displayed() -> None:
    """The penalty has to bite: lower displayed odds must mean fewer wins."""
    high = sum(R.resolve_pick(s, 1, [90, 10, 10], 0) for s in range(N)) / N
    low = sum(R.resolve_pick(s, 1, [20, 10, 10], 0) for s in range(N)) / N
    assert high == pytest.approx(0.90, abs=0.02)
    assert low == pytest.approx(0.20, abs=0.02)


def test_the_store_sells_one_harmless_cosmetic() -> None:
    assert R.STORE_ITEM_ID == "gold_frame"
    assert R.STORE_ITEM_PRICE == 100
    assert R.START_GEMS == 500


def test_calibration_keeps_the_game_worth_playing() -> None:
    from stats.calibrate import run

    result = run(2_000)
    assert 0.60 <= result["win_rate"] <= 0.68, result["win_rate"]
    assert result["losses_mean"] > 2.0, "too few losses would starve M1 and M2"
