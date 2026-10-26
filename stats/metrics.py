"""Per-session metrics (SPEC section 8). The session is the unit of analysis.

Reads the evidence store and nothing else. It must never open oracle/: the
whole point of V5 is to compare these estimates against a ground truth the
estimator could not see.

A metric that has no value for a session is None, not zero. A session with no
losses has no offers-per-loss, and averaging a zero in would quietly pull the
estimate toward the null.
"""

from __future__ import annotations

import json
import re
import statistics
from dataclasses import dataclass, asdict

LEVELS_TOTAL = 10

METRIC_IDS = ("M1", "M2", "M3", "M4", "B1")


@dataclass
class SessionMetrics:
    session_id: str
    run_id: str
    persona: str
    seed: int
    status: str
    compliant: bool
    levels_resolved: int
    wins: int
    losses: int
    offers: int
    picks: int
    best_picks: int
    m1: float | None = None   # offers shown / losses
    m2: float | None = None   # mean best displayed chance on post-loss levels
    m3: float | None = None   # win rate over 10 levels
    m4: float | None = None   # mean offer price
    b1: float | None = None   # share of picks taking the best displayed door

    def value(self, metric: str) -> float | None:
        return getattr(self, metric.lower())

    def as_row(self) -> dict:
        return asdict(self)


def _events(conn, session_id: str):
    rows = conn.execute(
        "SELECT type, payload FROM events WHERE session_id=? ORDER BY seq", (session_id,)
    ).fetchall()
    for row in rows:
        payload = json.loads(row[1]) if row[1] else {}
        yield row[0], payload


def session_metrics(conn, session_id: str) -> SessionMetrics:
    row = conn.execute(
        "SELECT run_id, persona, seed, status FROM sessions WHERE session_id=?", (session_id,)
    ).fetchone()
    run_id, persona, seed, status = (row[0], row[1], row[2], row[3]) if row else ("", "", 0, "")

    wins = losses = offers = picks = best_picks = 0
    post_loss_best: dict[int, float] = {}
    offer_prices: list[float] = []
    compliant = True

    for etype, payload in _events(conn, session_id):
        if etype == "level_end":
            if payload.get("result") == "WIN":
                wins += 1
            elif payload.get("result") == "LOSS":
                losses += 1
            picks += 1
            if payload.get("chose_best"):
                best_picks += 1
        elif etype == "level_start" and payload.get("follows_loss"):
            # Keyed by level, so a retry on the same level cannot count twice.
            post_loss_best[int(payload["level"])] = float(payload["best_chance"])
        elif etype == "offer_shown":
            offers += 1
            price = payload.get("price")
            if price is not None:
                offer_prices.append(float(price))
        elif etype == "session_end":
            compliant = bool(payload.get("compliant", True))

    m = SessionMetrics(
        session_id=session_id, run_id=run_id, persona=persona, seed=seed, status=status,
        compliant=compliant, levels_resolved=wins + losses, wins=wins, losses=losses,
        offers=offers, picks=picks, best_picks=best_picks,
    )
    m.m1 = (offers / losses) if losses else None
    m.m2 = statistics.fmean(post_loss_best.values()) if post_loss_best else None
    # Over levels actually resolved: a session cut short must not read as losses.
    m.m3 = (wins / (wins + losses)) if (wins + losses) else None
    m.m4 = statistics.fmean(offer_prices) if offer_prices else None
    m.b1 = (best_picks / picks) if picks else None
    return m


# The walker records blocked requests under a pseudo-session so they are
# hash-chained; it is not gameplay and must not enter the analysis.
NON_GAMEPLAY_PERSONAS = ("guard",)
_ATTEMPT_SUFFIX = re.compile(r"-a\d+$")


def run_metrics(conn, run_id: str) -> list[SessionMetrics]:
    placeholders = ",".join("?" * len(NON_GAMEPLAY_PERSONAS))
    # A retried session leaves its failed attempt in the store (it is attrition,
    # and stays verifiable), but only the final attempt of each job
    # (session id without its -aN suffix)
    # enters the analysis.
    rows = conn.execute(
        f"SELECT session_id, persona, seed, COALESCE(attempts, 1) FROM sessions"
        f" WHERE run_id=? AND persona NOT IN ({placeholders}) ORDER BY session_id",
        (run_id, *NON_GAMEPLAY_PERSONAS),
    ).fetchall()
    final: dict[str, tuple[int, str]] = {}
    for session_id, persona, seed, attempts in rows:
        key = _ATTEMPT_SUFFIX.sub("", session_id)
        if key not in final or attempts > final[key][0]:
            final[key] = (attempts, session_id)
    ids = sorted(sid for _, sid in final.values())
    return [session_metrics(conn, sid) for sid in ids]


def by_persona(rows: list[SessionMetrics], metric: str,
               *, exclude_deviations: bool = False) -> dict[str, list[float]]:
    """Group a metric's available values by persona, dropping absent values."""
    out: dict[str, list[float]] = {}
    for row in rows:
        if exclude_deviations and row.status == "protocol_deviation":
            continue
        value = row.value(metric)
        if value is None:
            continue
        out.setdefault(row.persona, []).append(float(value))
    return out


def contrast_values(rows: list[SessionMetrics], metric: str, a: str, b: str,
                    *, exclude_deviations: bool = False) -> tuple[list[float], list[float]]:
    grouped = by_persona(rows, metric, exclude_deviations=exclude_deviations)
    return grouped.get(a, []), grouped.get(b, [])


def missing_counts(rows: list[SessionMetrics]) -> dict[str, int]:
    """How many sessions had no value for each metric. Always reported."""
    return {m: sum(1 for r in rows if r.value(m) is None) for m in METRIC_IDS}


def persist(store, rows: list[SessionMetrics]) -> None:
    for row in rows:
        for metric in METRIC_IDS:
            store.set_metric(row.session_id, metric, row.value(metric))
