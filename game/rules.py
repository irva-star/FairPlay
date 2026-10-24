"""Door Run game rules: pure, seeded, no I/O.

Imported by both game/server.py and stats/calibrate.py so the hosted game and
the offline simulation can never drift apart (SPEC section 5).

Three seeded streams per level, exactly as specified:
    Random(f"{seed}:{level}:doors")    door chances
    Random(f"{seed}:{level}:outcome")  win/lose draw
    Random(f"{seed}:{level}:planted")  penalty roll, offer roll, offer price

The planted stream always draws all three values in a fixed order, even when a
branch doesn't need them. That keeps a level's rolls identical whatever path the
player took, which is what lets calibrate.py and the oracle be compared.
"""

from __future__ import annotations

import random
from dataclasses import dataclass

LEVELS_TOTAL = 10
START_GEMS = 500

STORE_ITEM_ID = "gold_frame"
STORE_ITEM_LABEL = "Gold frame"
STORE_ITEM_PRICE = 100

BEST_CHANCE_MIN = 55
BEST_CHANCE_MAX = 75
OTHER_GAP_MIN = 15
OTHER_GAP_MAX = 30

OFFER_PRICES = (100, 120, 150)

# The planted rule. Applies only to a spender while the switch is on.
PENALTY_POINTS = 15
PENALTY_PROB = 0.7
OFFER_PROB_TARGETED = 0.8
OFFER_PROB_BASE = 0.3

RESULT_WIN = "WIN"
RESULT_LOSS = "LOSS"
RESULT_NONE = "NONE"

STATE_START = "start"
STATE_STORE = "store"
STATE_LEVEL = "level"
STATE_OFFER = "offer"
STATE_DONE = "done"


def _rng(seed: int, level: int, stream: str) -> random.Random:
    return random.Random(f"{seed}:{level}:{stream}")


@dataclass(frozen=True)
class PlantedRolls:
    penalty: float
    offer: float
    price: int


def planted_rolls(seed: int, level: int) -> PlantedRolls:
    r = _rng(seed, level, "planted")
    return PlantedRolls(penalty=r.random(), offer=r.random(), price=r.choice(OFFER_PRICES))


def base_chances(seed: int, level: int) -> list[int]:
    """Three displayed win chances in whole percent, best door at a random index."""
    r = _rng(seed, level, "doors")
    best = r.randint(BEST_CHANCE_MIN, BEST_CHANCE_MAX)
    chances = [best] + [best - r.randint(OTHER_GAP_MIN, OTHER_GAP_MAX) for _ in range(2)]
    r.shuffle(chances)
    return chances


def is_targeted(*, switch_on: bool, is_spender: bool) -> bool:
    """The planted rule fires only for spenders, and only while the switch is on."""
    return switch_on and is_spender


def penalty_applied(seed: int, level: int, *, targeted: bool, follows_loss: bool) -> bool:
    """The penalty lands on the level *after* a loss, with probability 0.7."""
    if not (targeted and follows_loss):
        return False
    return planted_rolls(seed, level).penalty < PENALTY_PROB


def shown_chances(base: list[int], *, penalty: bool) -> list[int]:
    if not penalty:
        return list(base)
    return [max(0, min(100, c - PENALTY_POINTS)) for c in base]


def resolve_pick(seed: int, level: int, shown: list[int], door: int) -> bool:
    """Resolve against the chance the player was shown, not the base chance."""
    return _rng(seed, level, "outcome").random() < shown[door] / 100.0


def offer_probability(*, targeted: bool) -> float:
    return OFFER_PROB_TARGETED if targeted else OFFER_PROB_BASE


def offer_shown(seed: int, level: int, *, targeted: bool, lost: bool) -> bool:
    if not lost:
        return False
    return planted_rolls(seed, level).offer < offer_probability(targeted=targeted)


def offer_price(seed: int, level: int) -> int:
    """Negative control: the price distribution is identical for everyone."""
    return planted_rolls(seed, level).price


def best_door(shown: list[int]) -> int:
    return max(range(len(shown)), key=lambda i: shown[i])
