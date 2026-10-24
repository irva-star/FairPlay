"""Validation page: V4 calibration and V5 recovery against ground truth.

V5 is read from the JSON that validation/score_against_oracle.py wrote. The
dashboard never opens oracle/ itself -- that read happens in exactly one module,
which is what makes "the estimator could not see the answer" checkable.
"""

from __future__ import annotations

import json
from pathlib import Path

import plotly.graph_objects as go
import streamlit as st

from dashboard.common import IS_PUBLIC, METRIC_LABELS, page_header, query, run_picker

page_header("Validation", "Does the method find what is there, and stay quiet when nothing is?")

run_id = run_picker()
if not run_id:
    st.stop()

# --- V5 recovery -------------------------------------------------------------

st.subheader("V5 — recovery against ground truth")
scores_dir = Path("snapshot/validation" if IS_PUBLIC else "data/validation")
scores_path = scores_dir / f"{run_id}.json"

if not scores_path.exists():
    st.info(
        f"No V5 scores at `{scores_path}`. Generate them with:\n\n"
        f"`python -m validation.score_against_oracle {run_id}`"
    )
else:
    data = json.loads(scores_path.read_text(encoding="utf-8"))
    rows = data["rows"]
    cols = st.columns(len(rows))
    for col, row in zip(cols, rows):
        col.markdown(f"**{row['metric']}** " + ("✅" if row["recovered"] else "❌"))
        col.caption(METRIC_LABELS.get(row["metric"], ""))
        if row["truth"] is not None:
            col.metric("true effect", f"{row['truth']:+.3f}")
        if row["estimate"] is not None:
            col.caption(f"estimated {row['estimate']:+.3f}")
            col.caption(f"95% CI {row['ci_low']:+.3f} to {row['ci_high']:+.3f}")

    fig = go.Figure()
    for i, row in enumerate(rows):
        if row["estimate"] is None:
            continue
        fig.add_trace(go.Scatter(
            x=[row["estimate"]], y=[row["metric"]], mode="markers",
            marker=dict(size=12, color="#2563eb", symbol="diamond"),
            error_x=dict(type="data", symmetric=False,
                         array=[row["ci_high"] - row["estimate"]],
                         arrayminus=[row["estimate"] - row["ci_low"]],
                         color="#2563eb", thickness=2, width=8),
            name="estimate", showlegend=i == 0,
        ))
        if row["truth"] is not None:
            fig.add_trace(go.Scatter(
                x=[row["truth"]], y=[row["metric"]], mode="markers",
                marker=dict(size=14, color="#dc2626", symbol="x"),
                name="truth from oracle", showlegend=i == 0,
            ))
    fig.update_layout(height=120 + 70 * len(rows), margin=dict(l=10, r=10, t=10, b=10),
                      xaxis_title="effect, raw units")
    st.plotly_chart(fig, use_container_width=True)
    st.caption("The red × is what the server actually did. V5 passes when it falls inside "
               "the blue interval the pipeline reported without seeing it.")

st.divider()

# --- V4 calibration ----------------------------------------------------------

st.subheader("V4 — calibration: how often does it cry wolf?")
st.write(
    "Sessions that were never treated differently (every `free` and `free_twin` session, "
    "pooled across both batches) are split at random and put through the whole analysis "
    "1,000 times. About 5% of splits should alarm. More than 7% means the method is not "
    "calibrated and the headline findings cannot be trusted."
)

run_v4 = st.button("Run the 1,000-split calibration", type="primary")


@st.cache_data(show_spinner="Splitting and re-analysing 1,000 times…")
def calibration(run_id: str) -> dict:
    import yaml

    from dashboard.common import get_conn
    from stats.metrics import METRIC_IDS, run_metrics
    from stats.power import calibration_v4

    prereg = yaml.safe_load(Path("prereg.yaml").read_text(encoding="utf-8"))
    margins = {k: float(v["margin"]) for k, v in prereg["metrics"].items()}

    rows = [r for r in run_metrics(get_conn(), run_id) if r.persona in ("free", "free_twin")]
    values = {m: [r.value(m) for r in rows] for m in METRIC_IDS if m != "B1"}
    return calibration_v4(values, margins)


if run_v4:
    pooled = query(
        "SELECT COUNT(*) AS n FROM sessions WHERE run_id=? AND persona IN ('free','free_twin')",
        (run_id,),
    )
    if not pooled or pooled[0]["n"] < 20:
        st.warning(f"Only {pooled[0]['n'] if pooled else 0} untreated sessions in this run. "
                   "V4 needs the full free + free_twin pool to be meaningful.")
    else:
        result = calibration(run_id)
        c1, c2, c3 = st.columns(3)
        c1.metric("Alarm rate", f"{result['alarm_rate']:.1%}",
                  delta="pass" if result["passes"] else "FAIL",
                  delta_color="normal" if result["passes"] else "inverse")
        c2.metric("Splits", result["splits"])
        c3.metric("Group size", result["group_size"])

        fig = go.Figure(go.Histogram(x=result["min_p_per_split"], nbinsx=50,
                                     marker_color="#64748b"))
        fig.add_vline(x=0.05, line=dict(color="#dc2626", dash="dash"),
                      annotation_text="α = .05")
        fig.update_layout(height=320, margin=dict(l=10, r=10, t=10, b=10),
                          xaxis_title="smallest Holm-adjusted p in the split",
                          yaxis_title="splits")
        st.plotly_chart(fig, use_container_width=True)
        st.caption("Per-metric alarm rates: " + ", ".join(
            f"{m} {r:.1%}" for m, r in result["alarm_rate_by_metric"].items()))
