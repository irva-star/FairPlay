"""Reveal StreamVault's hidden rule and score the audit against it.

    python -m validation.sv_reveal <run_id>

This is the moment in the demo where the judge's sealed choice is opened. It
is in validation/ because only validation/ may read oracle/. The oracle is
matched to the run by seed (seeds are unique per batch), so other runs'
sessions never leak into this run's truth.

For each metric it compares the TRUE difference (computed from the oracle,
renter minus member) with the engine's estimate and 95% CI from the results
table, and says whether the engine named the right rule.
"""

from __future__ import annotations

import argparse
import json
import statistics
import sys
from pathlib import Path

from evidence.store import DEFAULT_DB, EvidenceStore
from streamvault import rules as SR

ORACLE_PATH = Path("oracle/streamvault.jsonl")

# Which metric each rule moves. "none" moves nothing.
RULE_METRIC = {"fee": "S1", "maze": "S3", "preticked": "S4", "none": None}


def load_truth(oracle_path: Path, seeds: set[int]) -> list[dict]:
    if not oracle_path.exists():
        raise FileNotFoundError(f"no oracle at {oracle_path}; was the server run locally?")
    rows = []
    for line in oracle_path.read_text(encoding="utf-8").splitlines():
        rec = json.loads(line)
        if rec.get("event") == "checkout" and rec.get("seed") in seeds:
            rows.append(rec)
    return rows


def true_effects(rows: list[dict]) -> dict[str, float | None]:
    def diff(key) -> float | None:
        a = [key(r) for r in rows if r["segment"] == "renter"]
        b = [key(r) for r in rows if r["segment"] == "member"]
        if not a or not b:
            return None
        return statistics.fmean(a) - statistics.fmean(b)

    return {
        "S1": diff(lambda r: r["fee"]),
        "S3": diff(lambda r: r["maze_len"]),
        "S4": diff(lambda r: sum(r["preticked"])),
    }


def detected_metrics(conn, run_id: str) -> list[str]:
    rows = conn.execute(
        "SELECT metric, verdict FROM results WHERE run_id=? AND contrast='SV_audit'", (run_id,)
    ).fetchall()
    return [m for m, v in rows if v in ("Difference detected", "Detected, below the margin of concern")
            and m in ("S1", "S3", "S4")]


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="python -m validation.sv_reveal")
    ap.add_argument("run_id")
    ap.add_argument("--db", default=str(DEFAULT_DB))
    ap.add_argument("--oracle", default=str(ORACLE_PATH))
    ap.add_argument("--write", action="store_true",
                    help="also save data/validation/<run_id>-reveal.json for the dashboard")
    args = ap.parse_args(argv)

    with EvidenceStore(args.db) as store:
        seeds = {r[0] for r in store.conn.execute(
            "SELECT DISTINCT seed FROM sessions WHERE run_id=?", (args.run_id,))}
        if not seeds:
            print(f"no sessions for run {args.run_id}", file=sys.stderr)
            return 1
        truth_rows = load_truth(Path(args.oracle), seeds)
        rules = sorted({r["rule"] for r in truth_rows})
        if len(rules) != 1:
            print(f"expected one rule for this run, found {rules}", file=sys.stderr)
            return 1
        rule = rules[0]
        truth = true_effects(truth_rows)
        found = detected_metrics(store.conn, args.run_id)
        estimates = {m: store.conn.execute(
            "SELECT diff, ci_low, ci_high FROM results WHERE run_id=? AND contrast='SV_audit' AND metric=?",
            (args.run_id, m)).fetchone() for m in ("S1", "S3", "S4")}

    copies = sorted({r.get("copy", "") for r in truth_rows})
    expected = RULE_METRIC[rule]
    named = {"S1": "fee", "S3": "maze", "S4": "preticked"}
    engine_says = [named[m] for m in found] or ["none"]
    correct = engine_says == [rule]

    if args.write:
        out = Path("data/validation") / f"{args.run_id}-reveal.json"
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(json.dumps({
            "run_id": args.run_id, "copy": ",".join(c for c in copies if c), "rule": rule,
            "engine_named": engine_says, "correct": correct,
            "rows": [{"metric": m, "truth": truth[m],
                      "estimate": None if estimates[m] is None else estimates[m][0],
                      "ci_low": None if estimates[m] is None else estimates[m][1],
                      "ci_high": None if estimates[m] is None else estimates[m][2]}
                     for m in ("S1", "S3", "S4")],
        }, indent=2), encoding="utf-8")
        print(f"wrote {out}")

    print(f"Hidden judge rule:   {rule}")
    print(f"Engine named:        {', '.join(engine_says)}")
    print(f"Verdict:             {'CORRECT' if correct else 'MISSED'}")
    print()
    print(f"{'metric':<8}{'true diff':>12}{'estimate':>12}{'95% CI':>26}  inside?")
    for m in ("S1", "S3", "S4"):
        t, e = truth[m], estimates[m]
        if e is None or e[0] is None:
            print(f"{m:<8}{'' if t is None else f'{t:+.2f}':>12}{'n/a':>12}")
            continue
        inside = t is not None and e[1] is not None and e[1] <= t <= e[2]
        ci = f"[{e[1]:+.2f}, {e[2]:+.2f}]" if e[1] is not None else "n/a"
        mark = "yes" if inside else "no"
        print(f"{m:<8}{'' if t is None else f'{t:+.2f}':>12}{e[0]:>+12.2f}{ci:>26}  {mark}")
    if expected:
        print(f"\nPlanted size: {dict(S1=SR.FEE_PENALTY, S3=SR.MAZE_PENALTY, S4=len(SR.PRETICK_FORCED))[expected]}"
              f" on {expected}, renters only.")
    return 0 if correct else 2


if __name__ == "__main__":
    sys.exit(main())
