"""Evidence page: every agent step, what it saw, what it did and why.

In local mode this refreshes every 2 seconds, which is the view the live demo
shows while agents are still playing.
"""

from __future__ import annotations

from pathlib import Path

import streamlit as st

from dashboard.common import (
    IS_PUBLIC, page_header, parse_payload, query, run_picker,
)

page_header("Evidence", "Append-only, hash-chained. Each screenshot carries its SHA-256.")

run_id = run_picker()
if not run_id:
    st.stop()

sessions = query(
    "SELECT session_id, persona, seed, status, started_at FROM sessions "
    "WHERE run_id=? ORDER BY started_at", (run_id,),
)
if not sessions:
    st.info("No sessions in this run yet.")
    st.stop()

labels = {f"{s['persona']} · seed {s['seed']} · {s['status']}": s["session_id"] for s in sessions}
chosen = st.selectbox("Session", list(labels))
session_id = labels[chosen]

show_steps_only = st.checkbox("Agent steps only", value=False)


@st.fragment(run_every=2 if not IS_PUBLIC else None)
def timeline() -> None:
    """Local mode polls so the demo audience watches the run fill in."""
    rows = query(
        "SELECT seq, ts, type, level_no, payload, screenshot_id, row_hash FROM events "
        "WHERE session_id=? ORDER BY seq", (session_id,),
    )
    st.caption(f"{len(rows)} events")

    for row in rows:
        if show_steps_only and row["type"] != "agent_step":
            continue
        payload = parse_payload(row["payload"])
        level = f" · level {row['level_no']}" if row["level_no"] else ""
        title = f"`{row['seq']:03d}` **{row['type']}**{level}"

        if row["type"] == "agent_step":
            obs = payload.get("observation", {})
            tool = payload.get("tool")
            args = {k: v for k, v in (payload.get("args") or {}).items() if k != "reason"}
            reason = payload.get("reason", "")
            rejected = payload.get("rejected")

            with st.expander(f"{title} — {tool} {args or ''}"
                             + ("  ⚠️ rejected" if rejected else ""), expanded=False):
                left, right = st.columns(2)
                left.markdown("**What the agent saw**")
                left.json(obs, expanded=False)
                right.markdown("**What it did**")
                right.write(f"`{tool}` {args}")
                if reason:
                    right.markdown(f"_Its stated reason:_ “{reason}”")
                if rejected:
                    right.error(f"Rejected by the executor: {rejected}")
                right.caption(
                    f"stop_reason `{payload.get('stop_reason','')}` · "
                    f"{payload.get('input_tokens',0)} in / {payload.get('output_tokens',0)} out · "
                    f"cache read {payload.get('cache_read_input_tokens',0)} · "
                    f"${payload.get('usd',0):.6f} · {payload.get('latency_ms',0)} ms · "
                    f"{payload.get('model','')}"
                )
        else:
            with st.expander(title, expanded=False):
                st.json(payload, expanded=False)

        if row["screenshot_id"]:
            shot = query("SELECT path, sha256 FROM screenshots WHERE screenshot_id=?",
                         (row["screenshot_id"],))
            if shot:
                path, digest = Path(shot[0]["path"]), shot[0]["sha256"]
                cols = st.columns([1, 2])
                if path.exists():
                    cols[0].image(str(path), width=280)
                    cols[1].success(f"✅ verified · `{digest[:32]}…`")
                else:
                    cols[1].warning(f"file missing · recorded hash `{digest[:32]}…`")

        st.caption(f"row hash `{row['row_hash'][:32]}…`")


timeline()

st.divider()
blocked = query(
    "SELECT payload FROM events WHERE type='blocked_request' AND session_id LIKE ?",
    (f"{run_id}%",),
)
st.subheader("Blocked requests")
if blocked:
    st.write(f"{len(blocked)} request(s) to non-allowlisted hosts were aborted.")
    st.json([parse_payload(b["payload"]) for b in blocked][:50], expanded=False)
else:
    st.caption("None. The browser reached only the two allowlisted game origins.")
