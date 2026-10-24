"""`python -m stats <run_id>` — compute metrics and results from evidence.

Reads the evidence store only. The margins, alpha and contrasts all come from
the committed prereg.yaml rather than from flags, so the analysis cannot be
quietly re-specified after seeing the data; the prereg hash is stored on every
result row.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import sys
from pathlib import Path

from evidence.store import DEFAULT_DB, EvidenceStore
from stats import metrics as M
from stats.analysis import analyse_contrast
from stats.power import format_power_table, power_table

PREREG = Path("prereg.yaml")


def load_prereg() -> dict:
    import yaml

    return yaml.safe_load(PREREG.read_text(encoding="utf-8"))


def prereg_hash() -> str:
    return hashlib.sha256(PREREG.read_bytes()).hexdigest()


def contrasts_for(label: str, prereg: dict) -> dict[str, dict]:
    """Only the contrasts whose batch matches this run's switch setting."""
    batch = "on" if label.lower() in ("on", "pilot") else "off"
    return {name: spec for name, spec in prereg["contrasts"].items()
            if str(spec["batch"]).lower() == batch}


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="python -m stats")
    ap.add_argument("run_id")
    ap.add_argument("--db", default=str(DEFAULT_DB))
    ap.add_argument("--out", default=None, help="results.json path (default data/<run_id>.json)")
    ap.add_argument("--no-bootstrap", action="store_true",
                    help="skip the bootstrap CI (much faster while iterating)")
    ap.add_argument("--exclude-deviations", action="store_true",
                    help="sensitivity analysis: drop protocol-deviation sessions")
    args = ap.parse_args(argv)

    prereg = load_prereg()
    margins = {k: float(v["margin"]) for k, v in prereg["metrics"].items()}

    with EvidenceStore(args.db) as store:
        run = store.conn.execute("SELECT label FROM runs WHERE run_id=?", (args.run_id,)).fetchone()
        if run is None:
            print(f"no such run: {args.run_id}", file=sys.stderr)
            return 1
        label = run["label"]

        rows = M.run_metrics(store.conn, args.run_id)
        if not rows:
            print(f"run {args.run_id} has no sessions", file=sys.stderr)
            return 1
        M.persist(store, rows)

        missing = M.missing_counts(rows)
        print(f"run {args.run_id} ({label}): {len(rows)} sessions")
        print("  sessions missing each metric: "
              + ", ".join(f"{k} {v}" for k, v in missing.items()))
        noncompliant = [r.session_id for r in rows if not r.compliant]
        if noncompliant:
            print(f"  {len(noncompliant)} non-compliant session(s), kept in the analysis")

        # Power check from the observed spread, reported against the plan.
        import statistics

        sigmas = {}
        for metric in ("M1", "M2", "M3", "M4"):
            values = [r.value(metric) for r in rows if r.value(metric) is not None]
            if len(values) > 1:
                sigmas[metric] = statistics.pstdev(values)
        if sigmas:
            print("\n" + format_power_table(power_table(
                sigmas, margins, n_planned=int(prereg["sessions_per_persona"]))))

        payload: dict = {"run_id": args.run_id, "label": label,
                         "prereg_hash": prereg_hash(), "contrasts": {}}

        for name, spec in contrasts_for(label, prereg).items():
            a, b = spec["a"], spec["b"]
            by_metric = {
                metric: M.contrast_values(rows, metric, a, b,
                                          exclude_deviations=args.exclude_deviations)
                for metric in M.METRIC_IDS
            }
            available = {m: v for m, v in by_metric.items() if v[0] and v[1]}
            if not available:
                print(f"\n{name}: no sessions for {a} vs {b}, skipped")
                continue

            estimates = analyse_contrast(name, available, margins,
                                         bootstrap=not args.no_bootstrap)
            print(f"\n{name}  ({a} − {b})")
            for est in estimates:
                ci = ("n/a" if est.ci_low is None
                      else f"[{est.ci_low:+.4f}, {est.ci_high:+.4f}]")
                diff = "n/a" if est.diff is None else f"{est.diff:+.4f}"
                print(f"  {est.metric:<4} n={est.n_a:>3}/{est.n_b:<3} diff={diff:>10} "
                      f"95%CI={ci:<22} {est.verdict}")
                store.write_result(args.run_id, {
                    "contrast": name, "metric": est.metric, "n_a": est.n_a, "n_b": est.n_b,
                    "diff": est.diff, "ci_low": est.ci_low, "ci_high": est.ci_high,
                    "hedges_g": est.hedges_g, "p_raw": est.p_raw, "p_holm": est.p_holm,
                    "tost_p": est.tost_p, "margin": est.margin, "verdict": est.verdict,
                    "prereg_hash": prereg_hash(),
                })
            payload["contrasts"][name] = [est.as_row() for est in estimates]

    out = Path(args.out) if args.out else Path("data") / f"{args.run_id}-results.json"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(payload, indent=2, default=str), encoding="utf-8")
    print(f"\nwrote {out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
