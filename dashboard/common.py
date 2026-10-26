"""Shared dashboard helpers (SPEC section 9).

Two modes from one codebase:
  FAIRPLAY_MODE=local   data/evidence.db, approvals and export enabled
  FAIRPLAY_MODE=public  snapshot/evidence.db opened read-only, no write paths

This module deliberately imports neither `walker` nor `anthropic`. The deployed
dashboard has no API key and no reason to be able to start a run, so the code
that could do either is simply not reachable from it.
"""

from __future__ import annotations

import json
import os
import sqlite3
from pathlib import Path

import streamlit as st

MODE = os.environ.get("FAIRPLAY_MODE", "local").strip().lower()
IS_PUBLIC = MODE == "public"

LOCAL_DB = Path("data/evidence.db")
SNAPSHOT_DB = Path("snapshot/evidence.db")

# Every FairPlay page carries this. The engine found a rule we planted in a game
# we wrote; saying so on every screen is what keeps the demo honest.
BANNER = "TEST GAME, planted rule (demo)"

# Findings language. "Difference detected" is a measurement; "unfair" is a
# judgement about intent, which this evidence cannot support.
CANNOT_SHOW = (
    "This evidence shows *what* the game did differently, measured against a rule "
    "we planted ourselves. It cannot show intent, and it says nothing about any "
    "real company or product."
)

VERDICT_ICON = {
    "Difference detected": "🔴",
    "Detected, below the margin of concern": "🟠",
    "Equivalent: no meaningful difference": "🟢",
    "Inconclusive": "⚪",
}

METRIC_LABELS = {
    "M1": "Offers shown per loss",
    "M2": "Best door chance after a loss (points)",
    "M3": "Win rate over 10 levels",
    "M4": "Mean offer price (gems)",
    "B1": "Share of picks taking the best door",
    # StreamVault
    "S1": "Service fee at checkout (cents)",
    "S2": "Checkout total minus advertised price (cents)",
    "S3": "Clicks needed to finish cancelling",
    "S4": "Add-ons pre-ticked on arrival",
    "S5": "Basic plan price (negative control, cents)",
}

# Persona labels for StreamVault's headline panel.
SV_PERSONA_LABELS = {"renter": "Rented a movie first", "member": "Did not rent",
                     "member_twin": "Did not rent (A/A twin)"}

CONTRAST_LABELS = {
    "V1_sensitivity": "V1 — spender vs free, rule ON (should detect)",
    "V2_specificity": "V2 — spender vs free, rule OFF (should find nothing)",
    "V3_aa": "V3 — free_twin vs free, A/A (should find nothing)",
    "SV_audit": "StreamVault — renters vs members (does the hidden rule show?)",
    "SV_aa": "StreamVault — member_twin vs member, A/A (should find nothing)",
}


def db_path() -> Path:
    return SNAPSHOT_DB if IS_PUBLIC else LOCAL_DB


@st.cache_resource
def get_conn() -> sqlite3.Connection:
    path = db_path()
    # Public mode is read-only at the connection level, not just by convention.
    conn = sqlite3.connect(f"file:{path.as_posix()}?mode=ro", uri=True, check_same_thread=False)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA busy_timeout=5000")
    return conn


def have_data() -> bool:
    return db_path().exists()


def page_header(title: str, subtitle: str = "") -> None:
    st.title(title)
    st.caption(f"{BANNER} · mode: {MODE}")
    if subtitle:
        st.write(subtitle)
    if not have_data():
        st.warning(
            f"No evidence database at `{db_path()}`. "
            + ("Run `python scripts/export_snapshot.py` and commit snapshot/."
               if IS_PUBLIC else
               "Run a batch first, for example `python scripts/run_demo.py --batch pilot --llm fake`.")
        )
        st.stop()


def query(sql: str, params: tuple = ()) -> list[sqlite3.Row]:
    return get_conn().execute(sql, params).fetchall()


def runs() -> list[sqlite3.Row]:
    return query("SELECT * FROM runs ORDER BY created_at DESC")


def run_picker(label: str = "Run") -> str | None:
    rows = runs()
    if not rows:
        st.info("No runs recorded yet.")
        return None
    options = {f"{r['label']} · {r['run_id']} · {r['status']}": r["run_id"] for r in rows}
    choice = st.selectbox(label, list(options))
    return options[choice]


def results_for(run_id: str) -> list[sqlite3.Row]:
    return query(
        "SELECT * FROM results WHERE run_id=? ORDER BY contrast, metric, computed_at DESC",
        (run_id,),
    )


def total_spend(run_id: str | None = None) -> float:
    if run_id:
        row = query("SELECT COALESCE(SUM(usd),0) AS s FROM spend WHERE run_id=?", (run_id,))
    else:
        row = query("SELECT COALESCE(SUM(usd),0) AS s FROM spend")
    return float(row[0]["s"]) if row else 0.0


def parse_payload(raw: str | None) -> dict:
    if not raw:
        return {}
    try:
        return json.loads(raw)
    except json.JSONDecodeError:
        return {}


def prereg_hash(app: str = "doorrun") -> str:
    """Hash of the committed analysis plan, shown beside every result."""
    import hashlib

    path = Path("prereg_streamvault.yaml" if app == "streamvault" else "prereg.yaml")
    if not path.exists():
        return "(prereg.yaml missing)"
    return hashlib.sha256(path.read_bytes()).hexdigest()[:16]
