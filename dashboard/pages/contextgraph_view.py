"""ContextGraph page: the Entropy money path, drawn from the hand-typed seed file.

Certainty is a line style, so a reader can see at a glance which claims are
journalism and which are a company's own account of itself. The unknown edges
are not drawn as paths -- they are listed as questions public data cannot answer,
which is the honest shape of the evidence.
"""

from __future__ import annotations

from collections import deque

import plotly.graph_objects as go
import streamlit as st

from dashboard.common import page_header

page_header(
    "ContextGraph — Entropy reconstruction",
    "Typed by a human from CBC's public reporting. No code and no agent ever visited "
    "Entropy, an archive of it, or any extremist site.",
)


@st.cache_data(show_spinner=False)
def load_graph():
    from contextgraph.analyze import analyse
    from contextgraph.load import load

    graph = load()
    return graph, analyse(graph)


try:
    graph, analysis = load_graph()
except Exception as exc:  # noqa: BLE001 - a bad seed file must say why, not crash blank
    st.error(f"Could not load the graph: {exc}")
    st.stop()

CERTAINTY_DASH = {
    "journalism_finding": "solid",
    "self_reported_via_journalism": "dash",
    "inferred": "dot",
    "unknown": "dot",
}
CERTAINTY_COLOR = {
    "journalism_finding": "#1f2937",
    "self_reported_via_journalism": "#2563eb",
    "inferred": "#7c3aed",
    "unknown": "#9ca3af",
}
CERTAINTY_LABEL = {
    "journalism_finding": "Journalists report it as their own finding",
    "self_reported_via_journalism": "The company's own claim, as quoted by journalists",
    "inferred": "Analyst inference",
    "unknown": "A question public data cannot answer",
}


def layered_positions(graph) -> dict[str, tuple[float, float]]:
    """Left-to-right by distance from the donors, so money reads as flowing."""
    adjacency: dict[str, list[str]] = {n.id: [] for n in graph.nodes}
    for edge in graph.edges:
        if not edge.is_unknown:
            adjacency[edge.src].append(edge.dst)

    depth = {"viewers": 0}
    queue = deque(["viewers"])
    while queue:
        node = queue.popleft()
        for nxt in adjacency.get(node, []):
            if nxt not in depth:
                depth[nxt] = depth[node] + 1
                queue.append(nxt)
    for node in adjacency:
        depth.setdefault(node, max(depth.values(), default=0) + 1)

    by_layer: dict[int, list[str]] = {}
    for node, d in sorted(depth.items()):
        by_layer.setdefault(d, []).append(node)

    pos: dict[str, tuple[float, float]] = {}
    for d, ids in by_layer.items():
        for i, node in enumerate(ids):
            pos[node] = (float(d), float(i) - (len(ids) - 1) / 2.0)
    return pos


pos = layered_positions(graph)
by_id = graph.by_id
chokepoint_ids = {c.node_id for c in analysis.chokepoints}
actionable_ids = {c.node_id for c in analysis.actionable}

fig = go.Figure()

for edge in graph.edges:
    if edge.is_unknown:
        continue  # a question is not a path
    x0, y0 = pos[edge.src]
    x1, y1 = pos[edge.dst]
    fig.add_trace(go.Scatter(
        x=[x0, x1], y=[y0, y1], mode="lines",
        line=dict(color=CERTAINTY_COLOR[edge.certainty], width=2,
                  dash=CERTAINTY_DASH[edge.certainty]),
        opacity=1.0 if edge.reviewed else 0.45,
        hovertemplate=(f"<b>{edge.id}</b> ({edge.kind})<br>{edge.claim}"
                       f"<br>certainty: {edge.certainty}"
                       f"<br>{'reviewed by ' + edge.reviewed_by if edge.reviewed else 'DRAFT — not yet reviewed'}"
                       "<extra></extra>"),
        showlegend=False,
    ))

node_x, node_y, labels, colors, sizes, hovers = [], [], [], [], [], []
for node in graph.nodes:
    x, y = pos[node.id]
    node_x.append(x)
    node_y.append(y)
    labels.append(node.label)
    if node.id in actionable_ids:
        colors.append("#dc2626")
    elif node.id in chokepoint_ids:
        colors.append("#f59e0b")
    else:
        colors.append("#64748b")
    sizes.append(30 if node.id in chokepoint_ids else 20)
    tag = ""
    if node.id in actionable_ids:
        tag = "<br><b>actionable chokepoint</b> (regulated)"
    elif node.id in chokepoint_ids:
        tag = "<br><b>chokepoint</b>"
    hovers.append(f"<b>{node.label}</b><br>{node.kind}{tag}<extra></extra>")

fig.add_trace(go.Scatter(
    x=node_x, y=node_y, mode="markers+text", text=labels, textposition="bottom center",
    marker=dict(size=sizes, color=colors, line=dict(color="white", width=2)),
    hovertemplate=hovers, showlegend=False,
))

fig.update_layout(
    height=520, margin=dict(l=10, r=10, t=10, b=10),
    xaxis=dict(visible=False), yaxis=dict(visible=False),
    plot_bgcolor="rgba(0,0,0,0)",
)
st.plotly_chart(fig, use_container_width=True)

legend = " · ".join(f"**{k.replace('_', ' ')}**: {v}" for k, v in CERTAINTY_LABEL.items())
st.caption("Line style shows certainty. " + legend)
if graph.unreviewed:
    st.warning(
        f"{len(graph.unreviewed)} edge(s) are still drafts, drawn faded: "
        f"{', '.join(e.id for e in graph.unreviewed)}. The curator must confirm each claim "
        "against the article before filling `reviewed_by`."
    )

st.divider()
left, right = st.columns(2)

with left:
    st.subheader("Chokepoints")
    if analysis.every_path_is_regulated:
        names = ", ".join(c.label for c in analysis.actionable)
        st.success(f"Every donor-to-streamer money path passes through a regulated node: {names}.")
        st.caption(
            "That is where the case actually broke: CBC reported that after the documentary "
            "aired, banking scrutiny led to the Georgian account being closed and the site "
            "announcing its shutdown. Moderation of words would not have reached it."
        )
    for c in analysis.chokepoints:
        st.markdown(f"- **{c.label}** — {c.tag} ({c.kind})")
    st.caption(f"{len(analysis.paths)} simple money path(s) found.")

with right:
    st.subheader("What public data cannot show")
    for u in analysis.unknowns:
        st.markdown(f"- **{u['question']}**")
        st.caption(f"{u['src']} → {u['dst']}")
    st.caption("These are modelled as questions, not as edges, so the drawing never implies "
               "a number nobody has published.")

st.divider()
st.subheader("Edge inspector")
rows = [
    {
        "id": e.id, "from": by_id[e.src].label, "to": by_id[e.dst].label, "kind": e.kind,
        "certainty": e.certainty, "claim or question": e.claim or e.question,
        "source": e.source_url or "—", "reviewed by": e.reviewed_by or "DRAFT",
    }
    for e in graph.edges
]
st.dataframe(rows, use_container_width=True, hide_index=True)
st.caption(graph.meta.get("rules", ""))
