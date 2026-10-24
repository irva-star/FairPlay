"""T2 calibration: simulate best-door play with game/rules.py, no browser.

Answers two questions the batch design depends on:
  * baseline win rate, which must sit in 60-68% so the game is neither a
    walkover nor hopeless (if it drifts out, change the base chance ranges in
    game/rules.py, not this file);
  * losses per session, because M1 and M2 are undefined for a session with zero
    losses, and the power check needs to know how often that happens.

Standard library only, so this runs before any dependency is installed.

    python -m stats.calibrate [--sessions 10000]
"""

from __future__ import annotations

import argparse
import statistics
import sys
from dataclasses import dataclass

from game import rules as R

WIN_RATE_TARGET = (0.60, 0.68)


@dataclass
class SessionSim:
    wins: int
    losses: int
    offers: int
    best_door_picks: int
    post_loss_best_chances: list[int]


def simulate_session(seed: int, *, switch_on: bool, is_spender: bool) -> SessionSim:
    """Play one session always choosing the best displayed door."""
    targeted = R.is_targeted(switch_on=switch_on, is_spender=is_spender)
    wins = losses = offers = best_picks = 0
    post_loss_best: list[int] = []
    last_was_loss = False

    for level in range(1, R.LEVELS_TOTAL + 1):
        base = R.base_chances(seed, level)
        penalty = R.penalty_applied(seed, level, targeted=targeted, follows_loss=last_was_loss)
        shown = R.shown_chances(base, penalty=penalty)
        if last_was_loss:
            post_loss_best.append(max(shown))

        door = R.best_door(shown)
        best_picks += 1
        won = R.resolve_pick(seed, level, shown, door)

        if won:
            wins += 1
        else:
            losses += 1
            if R.offer_shown(seed, level, targeted=targeted, lost=True):
                offers += 1
        last_was_loss = not won

    return SessionSim(wins, losses, offers, best_picks, post_loss_best)


def run(sessions: int, *, switch_on: bool = False, is_spender: bool = False) -> dict:
    sims = [simulate_session(s, switch_on=switch_on, is_spender=is_spender) for s in range(sessions)]
    levels = sessions * R.LEVELS_TOTAL
    wins = sum(s.wins for s in sims)
    losses = [s.losses for s in sims]
    zero_loss = sum(1 for n in losses if n == 0)
    offers = sum(s.offers for s in sims)
    no_offer = sum(1 for s in sims if s.offers == 0)
    m1 = [s.offers / s.losses for s in sims if s.losses]
    m2 = [statistics.fmean(s.post_loss_best_chances) for s in sims if s.post_loss_best_chances]
    m3 = [s.wins / R.LEVELS_TOTAL for s in sims]
    return {
        "sessions": sessions,
        "win_rate": wins / levels,
        "losses_mean": statistics.fmean(losses),
        "losses_sd": statistics.pstdev(losses),
        "zero_loss_sessions": zero_loss,
        "offer_rate_per_loss": offers / sum(losses) if sum(losses) else 0.0,
        "sessions_without_offer": no_offer,
        "m1_sd": statistics.pstdev(m1) if len(m1) > 1 else 0.0,
        "m2_sd": statistics.pstdev(m2) if len(m2) > 1 else 0.0,
        "m3_sd": statistics.pstdev(m3) if len(m3) > 1 else 0.0,
    }


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="Calibrate Door Run by simulation.")
    ap.add_argument("--sessions", type=int, default=10_000)
    args = ap.parse_args(argv)

    base = run(args.sessions)
    lo, hi = WIN_RATE_TARGET
    print(f"Best-door baseline over {base['sessions']} sessions "
          f"({base['sessions'] * R.LEVELS_TOTAL} levels)")
    print(f"  win rate                 {base['win_rate']:.4f}   target {lo:.2f}-{hi:.2f}")
    print(f"  losses per session       {base['losses_mean']:.2f} (sd {base['losses_sd']:.2f})")
    print(f"  sessions with 0 losses   {base['zero_loss_sessions']}  (no M1/M2 value)")
    print(f"  offers per loss          {base['offer_rate_per_loss']:.4f}   expect ~0.30")
    print(f"  sessions with no offer   {base['sessions_without_offer']}  (no M4 value)")
    print(f"  sd  M1 {base['m1_sd']:.4f}   M2 {base['m2_sd']:.4f}   M3 {base['m3_sd']:.4f}")

    planted = run(args.sessions, switch_on=True, is_spender=True)
    print("\nSame play as a spender with the switch ON (the planted effect)")
    print(f"  win rate                 {planted['win_rate']:.4f}  "
          f"(delta {planted['win_rate'] - base['win_rate']:+.4f})")
    print(f"  offers per loss          {planted['offer_rate_per_loss']:.4f}  "
          f"(delta {planted['offer_rate_per_loss'] - base['offer_rate_per_loss']:+.4f})")

    ok = lo <= base["win_rate"] <= hi
    print("\nCALIBRATION", "OK" if ok else "OUT OF RANGE: adjust base chance ranges in game/rules.py")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
