"""Results page: headline numbers, verdict cards, forest plots and the results table."""

from __future__ import annotations

import plotly.graph_objects as go
import streamlit as st
from plotly.subplots import make_subplots

from dashboard.common import (
    IS_PUBLIC, CANNOT_SHOW, CONTRAST_LABELS, METRIC_LABELS, SV_PERSONA_LABELS, VERDICT_ICON,
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

run_row = query("SELECT app FROM runs WHERE run_id=?", (run_id,))
app = (run_row[0]["app"] if run_row else None) or "doorrun"

if app == "streamvault":
    # The numbers anyone understands, before any statistics: what each kind of
    # member was actually charged, and how hard it was to leave.
    st.subheader("What members actually experienced")
    avg = {(r["persona"], r["metric"]): r["v"] for r in query(
        "SELECT s.persona, m.metric, AVG(m.value) AS v FROM metrics m "
        "JOIN sessions s ON s.session_id = m.session_id WHERE s.run_id=? "
        "GROUP BY s.persona, m.metric", (run_id,))}
    personas = [p for p in ("renter", "member", "member_twin") if (p, "S2") in avg]
    for persona in personas:
        paid = 499 + avg[(persona, "S2")]
        cols = st.columns(4)
        cols[0].markdown(f"**{SV_PERSONA_LABELS.get(persona, persona)}**")
        cols[1].metric("Advertised → charged", f"$4.99 → ${paid / 100:.2f}",
                       delta=f"+{(paid - 499) / 499:.0%} above advertised", delta_color="inverse")
        if (persona, "S3") in avg:
            cols[2].metric("Clicks to cancel", f"{avg[(persona, 'S3')]:.1f}",
                           help="Clicks on 'Continue to cancel'. Joining took 3.")
        if (persona, "S4") in avg:
            cols[3].metric("Add-ons pre-ticked", f"{avg[(persona, 'S4')]:.1f}")
    if not personas:
        st.caption("Run `python -m stats <run_id>` to compute the per-session numbers.")

    import json
    from pathlib import Path

    reveal_path = Path("snapshot/validation" if IS_PUBLIC else "data/validation") / f"{run_id}-reveal.json"
    if reveal_path.exists():
        reveal = json.loads(reveal_path.read_text(encoding="utf-8"))
        with st.expander("Reveal the hidden rule (opens the sealed answer key)"):
            named = ", ".join(reveal["engine_named"])
            st.markdown(f"**Hidden rule:** `{reveal['rule']}` · **Engine named:** `{named}` · "
                        + ("**correct**" if reveal["correct"] else "**missed**"))
            st.dataframe(reveal["rows"], hide_index=True, use_container_width=True)
            st.caption("True difference computed from the server's sealed log for this run's "
                       "sessions only, next to the engine's estimate and 95% CI.")
    st.divider()

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
if total_spend(run_id) == 0:
    # Say so plainly: a $0 run means the scripted stand-in played, not Claude.
    st.warning("This run used the free scripted stand-in agent, not Claude. It tests the "
               "engine end to end; a run with the real model is labelled with its spend.")
st.caption(f"Pre-registration hash `{prereg_hash(app)}` · analysis plan committed before the runs.")


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
        fig.update_xaxes(title_text="difference (A − B), raw units", row=i, col=1)

    fig.update_layout(height=190 * len(metrics), margin=dict(l=10, r=10, t=40, b=10))
    return fig


for contrast in ("V1_sensitivity", "V2_specificity", "V3_aa", "SV_audit", "SV_aa"):
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
