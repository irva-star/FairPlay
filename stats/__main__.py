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
from stats import metrics as doorrun_metrics
from stats import sv_metrics
from stats.analysis import HOLM_FAMILY, analyse_contrast
from stats.power import format_power_table, power_table

PREREG = Path("prereg.yaml")
PREREGS = {"doorrun": Path("prereg.yaml"), "streamvault": Path("prereg_streamvault.yaml")}
METRICS = {"doorrun": doorrun_metrics, "streamvault": sv_metrics}
FAMILIES = {"doorrun": HOLM_FAMILY, "streamvault": sv_metrics.HOLM_FAMILY}


def load_prereg(app: str = "doorrun") -> dict:
    import yaml

    return yaml.safe_load(PREREGS[app].read_text(encoding="utf-8"))


def prereg_hash(app: str = "doorrun") -> str:
    return hashlib.sha256(PREREGS[app].read_bytes()).hexdigest()


RUN_SWITCHES = ("on", "off", "hidden")  # "hidden": StreamVault, whose rule the plan never names
PREREG_BATCHES = ("on", "off", "any")   # "any": runs on every run of that app


def contrasts_for(switch: str, prereg: dict) -> dict[str, dict]:
    """Only the contrasts whose batch matches this run's recorded switch.

    Chosen from the switch, never from the label: a label is free text, and
    YAML 1.1 once turned the labels "on"/"off" into True/False. Anything
    unexpected is refused rather than guessed, so a typo can't silently pick
    the wrong contrasts (audit C1).
    """
    if switch not in RUN_SWITCHES:
        raise ValueError(f"run switch must be one of {RUN_SWITCHES}, got {switch!r}")
    for name, spec in prereg["contrasts"].items():
        if spec["batch"] not in PREREG_BATCHES:
            raise ValueError(f"prereg contrast {name}: batch must be quoted \"on\", \"off\" "
                             f"or \"any\", got {spec['batch']!r}")
    return {name: spec for name, spec in prereg["contrasts"].items()
            if spec["batch"] in ("any", switch)}


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

    with EvidenceStore(args.db) as store:
        run = store.conn.execute("SELECT label, switch, app FROM runs WHERE run_id=?",
                                 (args.run_id,)).fetchone()
        if run is None:
            print(f"no such run: {args.run_id}", file=sys.stderr)
            return 1
        label = run["label"]
        app = run["app"] or "doorrun"
        M, family = METRICS[app], FAMILIES[app]
        prereg = load_prereg(app)
        margins = {k: float(v["margin"]) for k, v in prereg["metrics"].items()}
        # Older runs have no switch column value; their label is the best we have.
        switch = run["switch"] or ("on" if str(label).lower() in ("on", "pilot", "true") else "off")

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
        for metric in family:
            values = [r.value(metric) for r in rows if r.value(metric) is not None]
            if len(values) > 1:
                sigmas[metric] = statistics.pstdev(values)
        if sigmas:
            print("\n" + format_power_table(power_table(
                sigmas, margins, n_planned=int(prereg["sessions_per_persona"]))))

        payload: dict = {"run_id": args.run_id, "label": label, "app": app,
                         "prereg_hash": prereg_hash(app), "contrasts": {}}

        for name, spec in contrasts_for(switch, prereg).items():
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
                                         bootstrap=not args.no_bootstrap, family=family)
            # Plain ASCII: piped output on Windows is cp1252, which has no U+2212.
            print(f"\n{name}  ({a} - {b})")
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
                    "prereg_hash": prereg_hash(app),
                })
            payload["contrasts"][name] = [est.as_row() for est in estimates]

    out = Path(args.out) if args.out else Path("data") / f"{args.run_id}-results.json"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(payload, indent=2, default=str), encoding="utf-8")
    print(f"\nwrote {out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
