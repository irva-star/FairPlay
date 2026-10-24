"""Results page: verdict cards, forest plots for V1-V3, and the results table."""

from __future__ import annotations

import plotly.graph_objects as go
import streamlit as st
from plotly.subplots import make_subplots

from dashboard.common import (
    CANNOT_SHOW, CONTRAST_LABELS, METRIC_LABELS, VERDICT_ICON,
    page_header, prereg_hash, query, results_for, run_picker, total_spend,
)

# Colourblind-safe: the band and the point must stay distinguishable in print.
BAND = "rgba(120,130,150,0.18)"
POINT = "#2563eb"
ZERO = "#9aa4b2"

page_header("Results", "Each metric with its 95% confidence interval and the margin of concern.")

run_id = run_picker()
if not run_id:
    st.stop()

rows = results_for(run_id)
if not rows:
    st.info("No results computed for this run yet. Run the analysis step first.")
    st.stop()

by_contrast: dict[str, list] = {}
for row in rows:
    by_contrast.setdefault(row["contrast"], []).append(row)

sessions = query(
    "SELECT persona, status, COUNT(*) AS n FROM sessions WHERE run_id=? GROUP BY persona, status",
    (run_id,),
)
total = sum(r["n"] for r in sessions)
ok = sum(r["n"] for r in sessions if r["status"] == "ok")

c1, c2, c3 = st.columns(3)
c1.metric("Sessions", total)
c2.metric("Completed", ok, delta=f"{total - ok} attrition" if total != ok else None,
          delta_color="inverse")
c3.metric("API spend", f"${total_spend(run_id):.4f}")
st.caption(f"Pre-registration hash `{prereg_hash()}` · analysis plan committed before the runs.")


def forest(contrast_rows: list) -> go.Figure:
    """One row per metric, each on its own axis so raw units stay readable."""
    metrics = [r for r in contrast_rows if r["diff"] is not None]
    if not metrics:
        return go.Figure()

    fig = make_subplots(
        rows=len(metrics), cols=1, shared_xaxes=False, vertical_spacing=0.12,
        subplot_titles=[METRIC_LABELS.get(r["metric"], r["metric"]) for r in metrics],
    )

    for i, row in enumerate(metrics, start=1):
        margin = row["margin"]
        diff = row["diff"]
        lo, hi = row["ci_low"], row["ci_high"]

        if margin:
            fig.add_shape(type="rect", x0=-margin, x1=margin, y0=-0.4, y1=0.4,
                          fillcolor=BAND, line_width=0, layer="below", row=i, col=1)
        fig.add_shape(type="line", x0=0, x1=0, y0=-0.4, y1=0.4,
                      line=dict(color=ZERO, width=1, dash="dot"), row=i, col=1)

        fig.add_trace(
            go.Scatter(
                x=[diff], y=[0], mode="markers",
                marker=dict(size=12, color=POINT, symbol="diamond"),
                error_x=dict(type="data", symmetric=False,
                             array=[(hi - diff) if hi is not None else 0],
                             arrayminus=[(diff - lo) if lo is not None else 0],
                             color=POINT, thickness=2, width=8),
                hovertemplate=(f"{row['metric']}<br>estimate %{{x:.4f}}"
                               f"<br>95% CI {lo:.4f} to {hi:.4f}"
                               f"<br>margin ±{margin}<extra></extra>"),
                showlegend=False,
            ),
            row=i, col=1,
        )
        fig.update_yaxes(visible=False, range=[-0.5, 0.5], row=i, col=1)
        fig.update_xaxes(title_text="difference (A − free), raw units", row=i, col=1)

    fig.update_layout(height=190 * len(metrics), margin=dict(l=10, r=10, t=40, b=10))
    return fig


for contrast in ("V1_sensitivity", "V2_specificity", "V3_aa"):
    contrast_rows = by_contrast.get(contrast)
    if not contrast_rows:
        continue
    st.subheader(CONTRAST_LABELS.get(contrast, contrast))

    cards = st.columns(min(len(contrast_rows), 5))
    for card, row in zip(cards, contrast_rows):
        icon = VERDICT_ICON.get(row["verdict"], "⚪")
        card.markdown(f"**{row['metric']}** {icon}")
        card.caption(METRIC_LABELS.get(row["metric"], row["metric"]))
        card.markdown(f"{row['verdict']}")
        if row["diff"] is not None:
            card.caption(f"{row['diff']:+.4f}  (n={row['n_a']} vs {row['n_b']})")

    st.plotly_chart(forest(contrast_rows), use_container_width=True)
    st.caption("Shaded band is the pre-registered margin of concern. "
               "An interval inside the band is equivalence; one clear of zero is a difference.")

st.divider()
st.subheader("All results")
st.dataframe(
    [
        {
            "contrast": r["contrast"], "metric": r["metric"],
            "n A": r["n_a"], "n free": r["n_b"],
            "difference": r["diff"], "CI low": r["ci_low"], "CI high": r["ci_high"],
            "Hedges g": r["hedges_g"], "p (Holm)": r["p_holm"], "p (TOST)": r["tost_p"],
            "margin": r["margin"], "verdict": r["verdict"],
        }
        for r in rows
    ],
    use_container_width=True, hide_index=True,
)

st.info(CANNOT_SHOW)
