"""Per-session metrics for StreamVault. The session is the unit of analysis.

Reads the evidence store only, never oracle/. Every value comes from what the
agent was shown, so the engine names the hidden rule blind.

    S1  service fee shown at checkout (cents)            drip pricing
    S2  checkout total minus advertised price (cents)    price climb
    S3  "continue cancelling" clicks to finish           cancellation maze
    S4  add-ons already ticked on arrival                sneak into basket
    S5  Basic plan price shown (cents)                   negative control
    B1  chose the Basic plan (1 or 0)                    balance check

A metric with no value for a session (it never reached checkout, say) is None,
never zero, and the counts are reported.
"""

from __future__ import annotations

import json
import re
from dataclasses import asdict, dataclass

METRIC_IDS = ("S1", "S2", "S3", "S4", "S5", "B1")
HOLM_FAMILY = ("S1", "S2", "S3", "S4", "S5")
NON_GAMEPLAY_PERSONAS = ("guard",)
_ATTEMPT_SUFFIX = re.compile(r"-a\d+$")


@dataclass
class SvSessionMetrics:
    session_id: str
    run_id: str
    persona: str
    seed: int
    status: str
    compliant: bool
    kept_membership: int = 0
    s1: float | None = None
    s2: float | None = None
    s3: float | None = None
    s4: float | None = None
    s5: float | None = None
    b1: float | None = None

    def value(self, metric: str) -> float | None:
        return getattr(self, metric.lower())

    def as_row(self) -> dict:
        return asdict(self)


def session_metrics(conn, session_id: str) -> SvSessionMetrics:
    row = conn.execute(
        "SELECT run_id, persona, seed, status FROM sessions WHERE session_id=?", (session_id,)
    ).fetchone()
    run_id, persona, seed, status = (row[0], row[1], row[2], row[3]) if row else ("", "", 0, "")
    m = SvSessionMetrics(session_id=session_id, run_id=run_id, persona=persona, seed=seed,
                         status=status, compliant=True)

    for etype, raw in conn.execute(
        "SELECT type, payload FROM events WHERE session_id=? ORDER BY seq", (session_id,)
    ):
        p = json.loads(raw) if raw else {}
        if etype == "addons_seen":
            m.s4 = float(p["preticked"])
            m.s5 = float(p["basic_price"])
        elif etype == "checkout_seen":
            m.s1 = float(p["fees"])
            m.s2 = float(p["total"] - p["headline"])
            m.b1 = 1.0 if p["plan"] == p["basic_price"] else 0.0
        elif etype == "cancelled":
            m.s3 = float(p["continue_clicks"])
            m.kept_membership = int(p.get("keep_clicks", 0))
        elif etype == "session_end":
            m.compliant = bool(p.get("compliant", True))
    return m


def run_metrics(conn, run_id: str) -> list[SvSessionMetrics]:
    """Final attempt of each job only; failed attempts stay in the store as attrition."""
    placeholders = ",".join("?" * len(NON_GAMEPLAY_PERSONAS))
    rows = conn.execute(
        f"SELECT session_id, COALESCE(attempts, 1) FROM sessions"
        f" WHERE run_id=? AND persona NOT IN ({placeholders}) ORDER BY session_id",
        (run_id, *NON_GAMEPLAY_PERSONAS),
    ).fetchall()
    final: dict[str, tuple[int, str]] = {}
    for session_id, attempts in rows:
        key = _ATTEMPT_SUFFIX.sub("", session_id)
        if key not in final or attempts > final[key][0]:
            final[key] = (attempts, session_id)
    return [session_metrics(conn, sid) for _, sid in sorted(final.values(), key=lambda t: t[1])]


def contrast_values(rows, metric: str, a: str, b: str, *, exclude_deviations: bool = False):
    def vals(persona: str) -> list[float]:
        return [float(r.value(metric)) for r in rows
                if r.persona == persona and r.value(metric) is not None
                and not (exclude_deviations and r.status == "protocol_deviation")]
    return vals(a), vals(b)


def missing_counts(rows) -> dict[str, int]:
    return {m: sum(1 for r in rows if r.value(m) is None) for m in METRIC_IDS}


def persist(store, rows) -> None:
    for row in rows:
        for metric in METRIC_IDS:
            store.set_metric(row.session_id, metric, row.value(metric))
