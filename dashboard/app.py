"""FairPlay Labs dashboard (SPEC section 9).

Local:   streamlit run dashboard/app.py
Public:  FAIRPLAY_MODE=public streamlit run dashboard/app.py \
             --server.address 0.0.0.0 --server.port $PORT

Pages are registered programmatically rather than auto-discovered, so in public
mode the Run plans and Sign-off pages are never constructed at all. A hidden
page that exists is a page someone can reach; one that was never registered
cannot be.
"""

from __future__ import annotations

import sys
from pathlib import Path

# `streamlit run dashboard/app.py` puts dashboard/ on sys.path, not the repo
# root, so the dashboard package and its siblings would not resolve without this.
REPO_ROOT = Path(__file__).resolve().parent.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

import streamlit as st  # noqa: E402

from dashboard.common import BANNER, IS_PUBLIC, MODE  # noqa: E402

st.set_page_config(page_title="FairPlay Labs", page_icon="⚖️", layout="wide")

PAGE_DIR = "dashboard/pages"

results = st.Page(f"{PAGE_DIR}/results.py", title="Results", icon="📊", default=True)
evidence = st.Page(f"{PAGE_DIR}/evidence.py", title="Evidence", icon="🔍")
validation = st.Page(f"{PAGE_DIR}/validation.py", title="Validation", icon="✅")
contextgraph = st.Page(f"{PAGE_DIR}/contextgraph_view.py", title="ContextGraph", icon="🕸️")
methods = st.Page(f"{PAGE_DIR}/methods.py", title="Methods and limits", icon="📄")

pages = [results, evidence, validation, contextgraph, methods]

if not IS_PUBLIC:
    run_plans = st.Page(f"{PAGE_DIR}/run_plans.py", title="Run plans", icon="📋")
    signoff = st.Page(f"{PAGE_DIR}/signoff.py", title="Sign-off and export", icon="✍️")
    pages = [run_plans, *pages, signoff]

st.sidebar.caption(BANNER)
st.sidebar.caption(f"mode: **{MODE}**" + ("  (read-only)" if IS_PUBLIC else ""))

st.navigation(pages).run()
