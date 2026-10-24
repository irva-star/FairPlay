"""V5 recovery: did the estimates land on the truth? (SPEC section 8)

This is the ONLY module permitted to read oracle/. stats/ must never see it,
because an estimator that can read the answer key proves nothing. Keeping the
read here, behind one function, is what makes that claim checkable.

The oracle log records what the server actually did per level: the base and
shown chances, whether the penalty fired, and whether an offer was shown. From
that we recompute M1 and M2 per session exactly as the dashboard's estimator
does from evidence, difference the two segments, and ask whether the true
difference falls inside the 95% CI the pipeline reported.
"""

from __future__ import annotations

import json
import statistics
from collections import defaultdict
from dataclasses import dataclass
from pathlib import Path

from game import rules as R

ORACLE_PATH = Path("oracle/ground_truth.jsonl")

# What the planted rule is designed to do, from the rules themselves.
THEORETICAL = {
    "M1": R.OFFER_PROB_TARGETED - R.OFFER_PROB_BASE,          # +0.50
    "M2": -R.PENALTY_PROB * R.PENALTY_POINTS,                  # -10.50
}


@dataclass
class OracleSession:
    pid: str
    seed: int
    segment: str
    switch: str
    losses: int = 0
    offers: int = 0
    post_loss_best: list[int] = None  # type: ignore[assignment]

    def __post_init__(self) -> None:
        if self.post_loss_best is None:
            self.post_loss_best = []

    @property
    def m1(self) -> float | None:
        return self.offers / self.losses if self.losses else None

    @property
    def m2(self) -> float | None:
        return statistics.fmean(self.post_loss_best) if self.post_loss_best else None


def load_oracle(path: str | Path = ORACLE_PATH) -> list[OracleSession]:
    """Read ground truth and rebuild per-session M1 and M2."""
    path = Path(path)
    if not path.exists():
        raise FileNotFoundError(
            f"no oracle log at {path}. Measured batches must run against the local "
            "server, which is the only place ground truth is written."
        )

    levels: dict[str, list[dict]] = defaultdict(list)
    for line in path.read_text(encoding="utf-8").splitlines():
        if line.strip():
            row = json.loads(line)
            levels[row["pid"]].append(row)

    sessions: list[OracleSession] = []
    for pid, rows in levels.items():
        rows.sort(key=lambda r: r["level_no"])
        s = OracleSession(pid=pid, seed=rows[0]["seed"], segment=rows[0]["segment"],
                          switch=rows[0]["switch"])
        # A session's segment is whatever it was by the end: a player who bought
        # at level 1 is a spender for the whole session.
        s.segment = "spender" if any(r["segment"] == "spender" for r in rows) else "free"
        previous_lost = False
        for row in rows:
            if previous_lost:
                s.post_loss_best.append(max(row["shown_chances"]))
            if not row["won"]:
                s.losses += 1
                if row["offer_shown"]:
                    s.offers += 1
            previous_lost = not row["won"]
        sessions.append(s)
    return sessions


def true_effect(sessions: list[OracleSession], metric: str, *, a: str = "spender",
                b: str = "free") -> float | None:
    key = (lambda s: s.m1) if metric == "M1" else (lambda s: s.m2)
    va = [key(s) for s in sessions if s.segment == a and key(s) is not None]
    vb = [key(s) for s in sessions if s.segment == b and key(s) is not None]
    if not va or not vb:
        return None
    return statistics.fmean(va) - statistics.fmean(vb)


@dataclass
class RecoveryRow:
    metric: str
    truth: float | None
    theoretical: float | None
    estimate: float | None
    ci_low: float | None
    ci_high: float | None

    @property
    def recovered(self) -> bool:
        if None in (self.truth, self.ci_low, self.ci_high):
            return False
        return self.ci_low <= self.truth <= self.ci_high


def score(conn, run_id: str, *, contrast: str = "V1_sensitivity",
          oracle_path: str | Path = ORACLE_PATH,
          metrics: tuple[str, ...] = ("M1", "M2")) -> list[RecoveryRow]:
    sessions = load_oracle(oracle_path)
    rows: list[RecoveryRow] = []
    for metric in metrics:
        result = conn.execute(
            "SELECT diff, ci_low, ci_high FROM results WHERE run_id=? AND contrast=? AND metric=?"
            " ORDER BY computed_at DESC LIMIT 1",
            (run_id, contrast, metric),
        ).fetchone()
        rows.append(RecoveryRow(
            metric=metric,
            truth=true_effect(sessions, metric),
            theoretical=THEORETICAL.get(metric),
            estimate=result[0] if result else None,
            ci_low=result[1] if result else None,
            ci_high=result[2] if result else None,
        ))
    return rows


SCORES_DIR = Path("data/validation")


def write_scores(run_id: str, rows: list[RecoveryRow], *,
                 directory: str | Path = SCORES_DIR) -> Path:
    """Persist the V5 result so the dashboard can show it without reading oracle/.

    This is the boundary: ground truth is read here and only here, and what
    leaves is a handful of numbers plus a pass/fail.
    """
    out = Path(directory)
    out.mkdir(parents=True, exist_ok=True)
    path = out / f"{run_id}.json"
    path.write_text(json.dumps({
        "run_id": run_id,
        "rows": [
            {
                "metric": r.metric, "truth": r.truth, "theoretical": r.theoretical,
                "estimate": r.estimate, "ci_low": r.ci_low, "ci_high": r.ci_high,
                "recovered": r.recovered,
            }
            for r in rows
        ],
        "all_recovered": all(r.recovered for r in rows),
    }, indent=2), encoding="utf-8")
    return path


def format_rows(rows: list[RecoveryRow]) -> str:
    lines = [f"{'metric':<8}{'truth':>10}{'expected':>10}{'estimate':>10}"
             f"{'95% CI low':>12}{'high':>10}  recovered"]
    for r in rows:
        def fmt(v: float | None) -> str:
            return "n/a" if v is None else f"{v:.4f}"
        lines.append(f"{r.metric:<8}{fmt(r.truth):>10}{fmt(r.theoretical):>10}"
                     f"{fmt(r.estimate):>10}{fmt(r.ci_low):>12}{fmt(r.ci_high):>10}"
                     f"  {'yes' if r.recovered else 'NO'}")
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    import argparse

    from evidence.store import DEFAULT_DB, EvidenceStore

    ap = argparse.ArgumentParser(description="V5: score estimates against ground truth.")
    ap.add_argument("run_id")
    ap.add_argument("--db", default=str(DEFAULT_DB))
    ap.add_argument("--contrast", default="V1_sensitivity")
    ap.add_argument("--oracle", default=str(ORACLE_PATH))
    args = ap.parse_args(argv)

    with EvidenceStore(args.db, read_only=True) as store:
        rows = score(store.conn, args.run_id, contrast=args.contrast, oracle_path=args.oracle)
    print(format_rows(rows))
    path = write_scores(args.run_id, rows)
    print(f"\nwrote {path}")
    return 0 if all(r.recovered for r in rows) else 1


if __name__ == "__main__":
    raise SystemExit(main())
