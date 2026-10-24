"""Run plans and human approval (rule S14). Local mode only.

A batch starts only after a named person has approved this exact plan, cost
estimate included. The plan hash covers the origin, the switch, the personas,
the model and the prompt hashes, so editing any of them invalidates the
approval instead of quietly reusing it.

This page is never registered in public mode, so the deployed dashboard has no
approval control to find.
"""

from __future__ import annotations

from pathlib import Path

import streamlit as st

from dashboard.common import IS_PUBLIC, page_header

if IS_PUBLIC:  # belt and braces: the page is not registered publicly either
    st.error("Run plans are not available in public mode.")
    st.stop()

page_header("Run plans", "Approve a batch before it runs. Approval is per plan, not standing.")

CONFIGS = {
    "Pilot (20 sessions, switch ON)": "configs/run_pilot.yaml",
    "Main ON batch (180 sessions)": "configs/run_on.yaml",
    "Main OFF batch (120 sessions)": "configs/run_off.yaml",
}

choice = st.selectbox("Batch", list(CONFIGS))
config_path = CONFIGS[choice]

if not Path(config_path).exists():
    st.error(f"Missing config: {config_path}")
    st.stop()

from evidence.store import EvidenceStore  # noqa: E402 - local mode only
from walker import guard  # noqa: E402
from walker.budget import load_pricing  # noqa: E402
from walker.cli import build_plan  # noqa: E402

pricing = load_pricing()
plan = build_plan(config_path)

try:
    guard.assert_config_origin(plan.origin)
    origin_ok = True
except ValueError as exc:
    origin_ok = False
    st.error(f"Origin refused by the network guard: {exc}")

with EvidenceStore("data/evidence.db") as store:
    spent = store.total_spend_usd()
    existing = store.find_approval(kind="run_plan", object_hash=plan.plan_hash)

estimate = plan.estimated_cost(pricing)
remaining = max(0.0, pricing.caps.run - spent)

c1, c2, c3, c4 = st.columns(4)
c1.metric("Sessions", plan.sessions)
c2.metric("Estimated calls", f"~{plan.estimated_calls}")
c3.metric("Estimated cost", f"${estimate:.4f}")
c4.metric("Remaining cap", f"${remaining:.4f}",
          delta=None if estimate <= remaining else "estimate exceeds cap",
          delta_color="inverse")

st.markdown(f"""
| | |
|---|---|
| origin | `{plan.origin}` |
| server switch | `PLANTED_SWITCH={plan.switch}` |
| model | `{plan.model}` |
| personas | {", ".join(f"{k} ×{v}" for k, v in sorted(plan.personas.items()))} |
| concurrency | {plan.concurrency} |
| max steps/session | {plan.max_steps} |
| system prompt sha256 | `{plan.system_prompt_hash[:32]}…` |
| tools sha256 | `{plan.tools_hash[:32]}…` |
| **plan hash** | `{plan.plan_hash}` |
""")

if estimate > remaining:
    st.error("This batch would exceed the run cap. `walker run` will refuse to start it.")

if existing:
    st.success(f"Already approved by **{existing['approver']}** at {existing['approved_at']}.")
    st.code(f"python -m walker run {config_path}", language="bash")
else:
    st.subheader("Approve this plan")
    with st.form("approve"):
        name = st.text_input("Your full name",
                             help="Recorded with the approval. Use the name you would sign with.")
        note = st.text_input("Note (optional)")
        confirm = st.checkbox(
            f"I have read this plan and approve spending about ${estimate:.4f} on it."
        )
        submitted = st.form_submit_button("Approve", type="primary", disabled=not origin_ok)

    if submitted:
        if not name.strip():
            st.error("A name is required: an approval nobody signed is not an approval.")
        elif not confirm:
            st.error("Tick the confirmation box to approve.")
        else:
            with EvidenceStore("data/evidence.db") as store:
                approval_id = store.record_approval(
                    kind="run_plan", object_hash=plan.plan_hash,
                    approver=name.strip(), note=note.strip(),
                )
            st.success(f"Approval {approval_id} recorded. Now run:")
            st.code(f"python -m walker run {config_path}", language="bash")

st.divider()
st.caption("Batches are started from a terminal, never from this page: the dashboard can "
           "record an approval but cannot spend money.")
