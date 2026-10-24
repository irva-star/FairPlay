"""Sign-off on findings and evidence-pack export. Local mode only.

Export is deliberately gated: the pack can only be built after a named person
has signed off on that run's findings. A finding that left the building without
a human attached to it is the failure mode this page exists to prevent.
"""

from __future__ import annotations

import hashlib
import io
import json
import zipfile
from pathlib import Path

import streamlit as st

from dashboard.common import (
    IS_PUBLIC, page_header, prereg_hash, query, results_for, run_picker,
)

if IS_PUBLIC:
    st.error("Sign-off and export are not available in public mode.")
    st.stop()

page_header("Sign-off and export", "Findings are signed by a person before anything leaves.")

run_id = run_picker()
if not run_id:
    st.stop()

from evidence.store import EvidenceStore  # noqa: E402 - local mode only

rows = results_for(run_id)
if not rows:
    st.info("No results to sign off on yet.")
    st.stop()

st.subheader("Findings to sign")
st.dataframe(
    [{"contrast": r["contrast"], "metric": r["metric"], "difference": r["diff"],
      "verdict": r["verdict"]} for r in rows],
    use_container_width=True, hide_index=True,
)

findings_hash = hashlib.sha256(
    json.dumps([[r["contrast"], r["metric"], r["diff"], r["verdict"]] for r in rows],
               sort_keys=True).encode()
).hexdigest()
st.caption(f"Findings hash `{findings_hash[:32]}…` — covers every row above, so editing a "
           "result invalidates the sign-off.")

with EvidenceStore("data/evidence.db") as store:
    finding_approval = store.find_approval(kind="finding", object_hash=findings_hash)

if finding_approval:
    st.success(f"Signed off by **{finding_approval['approver']}** "
               f"at {finding_approval['approved_at']}.")
else:
    with st.form("signoff"):
        name = st.text_input("Reviewer full name")
        note = st.text_area("Note (what you checked)")
        confirm = st.checkbox("I have reviewed these findings and the evidence behind them.")
        submitted = st.form_submit_button("Sign off", type="primary")
    if submitted:
        if not name.strip() or not confirm:
            st.error("A name and the confirmation are both required.")
        else:
            with EvidenceStore("data/evidence.db") as store:
                approval_id = store.record_approval(
                    kind="finding", object_hash=findings_hash,
                    approver=name.strip(), note=note.strip(),
                )
            st.success(f"Sign-off {approval_id} recorded.")
            st.rerun()

st.divider()
st.subheader("Evidence pack")

if not finding_approval:
    st.info("Export unlocks once the findings above are signed off.")
    st.stop()


def build_pack(run_id: str) -> bytes:
    """Manifest, results, prereg, methods note, sample screenshots, verify output."""
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w", zipfile.ZIP_DEFLATED) as zf:
        manifest = Path(f"data/manifests/{run_id}.json")
        if manifest.exists():
            zf.write(manifest, f"manifest/{manifest.name}")

        zf.writestr("results.json", json.dumps(
            [dict(r) for r in results_for(run_id)], indent=2, default=str))

        prereg = Path("prereg.yaml")
        if prereg.exists():
            zf.write(prereg, "prereg.yaml")

        scores = Path(f"data/validation/{run_id}.json")
        if scores.exists():
            zf.write(scores, "validation/v5_recovery.json")

        shots = query(
            "SELECT s.path FROM screenshots s JOIN events e ON e.screenshot_id=s.screenshot_id "
            "JOIN sessions ss ON ss.session_id=e.session_id WHERE ss.run_id=? LIMIT 8", (run_id,),
        )
        for row in shots:
            p = Path(row["path"])
            if p.exists():
                zf.write(p, f"screenshots/{p.name}")

        zf.writestr("METHODS.md", f"""# Evidence pack — run {run_id}

Signed off by {finding_approval['approver']} at {finding_approval['approved_at']}.

Pre-registration hash: {prereg_hash()}
Findings hash: {findings_hash}

## What this contains
A planted-difference audit of **our own test game**, Door Run. One rule was planted:
after a player buys a cosmetic, the game lowers their door odds following a loss and
shows purchase offers more often. LLM personas, identical but for one sentence about
buying, played the game; the engine then tried to detect the rule from evidence alone.

## What it cannot show
Intent. Nothing here is a finding about any real company, product or person. The
hash chain is tamper-evident, not tamper-proof.

## Verifying
    python -m evidence verify {run_id}
""")
    return buffer.getvalue()


st.download_button(
    "Download evidence pack (.zip)",
    data=build_pack(run_id),
    file_name=f"fairplay-evidence-{run_id}.zip",
    mime="application/zip",
    type="primary",
)
st.caption("Contains the manifest, results, pre-registration, V5 scores, sample "
           "screenshots and a one-page methods note.")
