"""StreamVault rules: pure, seeded, no I/O.

StreamVault is a fictional streaming service built to be audited. Every member
meets the same baseline dark patterns (a drip-priced service fee, low-contrast
auto-renewal fine print, a cancellation maze). On top of that, ONE hidden
"judge rule" can treat a target segment worse. The rule is chosen when the
server starts (env JUDGE_RULE) and no HTTP route can read or change it.

Target segment: a "renter", i.e. someone who rented a movie before signing up.
That mirrors the case study: a paying user is profiled as high-value and
squeezed harder.

Rules (only for renters, only when that rule is active):
    fee        service fee is $3.00 higher
    maze       cancellation takes 4 more "Are you sure?" screens
    preticked  two add-ons arrive already ticked at checkout
    none       nobody is treated differently (the specificity control)

Randomness is seeded per session, so every value is reproducible and the
oracle can be recomputed: Random(f"{seed}:sv:<stream>").
"""

from __future__ import annotations

import random
from dataclasses import dataclass

RULES = ("none", "fee", "maze", "preticked")

HEADLINE_PRICE = 499          # "From $4.99/month"
RENT_PRICE = 399
PLAN_PRICES = (499, 1099, 1699)
ADDON_PRICES = (199, 299, 599, 149)
ADDON_NAMES = ("Extra screen", "Offline downloads", "Sports pass", "Device protection")

BASE_FEES = (199, 249, 299)   # drip-priced "service & platform fee", everyone
MAZE_MIN, MAZE_MAX = 2, 4     # baseline "Are you sure?" screens, everyone
BASE_PRETICK_PROB = 0.15      # each add-on pre-ticked by default, everyone

FEE_PENALTY = 300             # rule "fee": +$3.00 for renters
MAZE_PENALTY = 4              # rule "maze": +4 screens for renters
PRETICK_FORCED = (0, 3)       # rule "preticked": these arrive ticked for renters

CANCEL_MESSAGES = (
    "Are you sure? You'll lose your watchlist and locked-in member pricing.",
    "Wait! Re-joining later won't include your current rate.",
    "Your Night Harbor progress will be reset.",
    "Members like you watch 14 hours a month. Stay for $1 less?",
    "We'll miss you. Can we pause your plan instead?",
    "Last chance: keep your household profiles?",
    "Almost done. Tell us why you're leaving, then confirm.",
    "Final confirmation: cancel your StreamVault membership?",
)


def _rng(seed: int, stream: str) -> random.Random:
    return random.Random(f"{seed}:sv:{stream}")


def parse_rule(raw: str | None) -> str:
    rule = (raw or "none").strip().lower()
    if rule not in RULES:
        raise ValueError(f"JUDGE_RULE must be one of {RULES}, got {raw!r}")
    return rule


@dataclass(frozen=True)
class Treatment:
    """Everything the server will show this session, decided at signup time."""
    fee: int
    maze_len: int
    preticked: tuple[bool, bool, bool, bool]
    rule_applied: bool


def treatment(seed: int, *, rule: str, renter: bool) -> Treatment:
    base_fee = _rng(seed, "fee").choice(BASE_FEES)
    base_maze = _rng(seed, "maze").randint(MAZE_MIN, MAZE_MAX)
    r = _rng(seed, "pretick")
    pre = [r.random() < BASE_PRETICK_PROB for _ in ADDON_PRICES]

    targeted = renter and rule != "none"
    fee, maze = base_fee, base_maze
    if targeted and rule == "fee":
        fee += FEE_PENALTY
    if targeted and rule == "maze":
        maze += MAZE_PENALTY
    if targeted and rule == "preticked":
        for i in PRETICK_FORCED:
            pre[i] = True
    return Treatment(fee=fee, maze_len=maze, preticked=tuple(pre), rule_applied=targeted)


def checkout_total(plan_price: int, addons: list[bool], fee: int) -> int:
    return plan_price + sum(p for p, on in zip(ADDON_PRICES, addons) if on) + fee
