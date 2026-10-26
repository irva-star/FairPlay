"""Methods and limits: the plain-language method, what it cannot show, and the safety rules."""

from __future__ import annotations

import streamlit as st

from dashboard.common import CANNOT_SHOW, page_header, prereg_hash

page_header("Methods and limits")

st.subheader("What this is")
st.markdown("""
We wrote a small browser game, **Door Run**, and planted one hidden rule in it: once a
player buys a cosmetic item, the game quietly treats them worse after a loss — their
door odds drop, and they get pushed a purchase offer far more often.

LLM agent personas then play the game a few hundred times. The personas are identical
except for a single sentence about whether to buy the cosmetic, so any difference in how
the game treats them traces back to that purchase and nothing else.

The engine then asks: can we detect the planted rule from the evidence alone, and — just
as importantly — does it stay quiet when we switch the rule off?
""")

st.subheader("How a difference is judged")
st.markdown("""
The unit of analysis is a **session**. Four metrics are pre-registered before the runs,
with a *margin of concern* for each: a difference smaller than the margin is not
practically interesting even if it is statistically real.

Each metric gets two tests:

- a **Welch** test, asking whether there is any difference at all (Holm-corrected across
  the four metrics, because testing four things raises the chance of a fluke);
- a **TOST equivalence** test, asking whether any difference is small enough to call the
  groups equivalent.

Together they give four verdicts, and the fourth one matters most:

| Difference found | Equivalent | Verdict |
|---|---|---|
| yes | no | Difference detected |
| yes | yes | Detected, below the margin of concern |
| no | yes | Equivalent: no meaningful difference |
| no | no | **Inconclusive** |

"Inconclusive" is never displayed as "no difference". Not finding something is not the
same as showing it is not there, and conflating the two is how audits mislead.
""")

st.subheader("Why we trust the method, not just the result")
st.markdown("""
A detector that always says "difference" would find our planted rule too. So the engine
is checked five ways:

- **V1 sensitivity** — with the rule on, it must detect the two affected metrics.
- **V2 specificity** — with the rule off, it must find nothing.
- **V3 A/A** — two groups that are byte-identical must come out equivalent.
- **V4 calibration** — splitting untreated sessions at random 1,000 times should raise a
  false alarm about 5% of the time, never more than 7%.
- **V5 recovery** — the true effect, computed from a sealed log the estimator cannot
  read, must fall inside the reported 95% confidence interval.
""")

st.subheader("Limits — what this cannot show")
st.warning(CANNOT_SHOW)
st.markdown("""
- **No intent.** We measure what the game did, not why. A difference is not proof that
  anyone decided to be unfair.
- **A game we wrote.** The planted rule is ours. Nothing here is a finding about any real
  company, product or person.
- **Tamper-evident, not tamper-proof.** The hash chain detects later edits; someone with
  write access could rebuild it. Committing each run's manifest hash to git before
  analysis is what closes most of that gap.
- **Agents, not people.** LLM personas are consistent and cheap, which is what makes the
  comparison clean, but they are not evidence about how humans would behave.
""")

st.subheader("Safety rules in force")
st.markdown("""
These are enforced in code and each is covered by a test, not left to good intentions:

- The browser can reach exactly **two origins**, both our own game. Every other request
  is aborted, and DNS fails for every other host.
- The model never sees raw page content — only integers and fixed enums that our code
  extracted and validated. No HTML, page text, URLs or screenshots.
- The agent's tools are a **closed list of five**. There is no navigation, typing, URL,
  file or shell tool, so the worst a hostile page could do is change which door gets picked.
- **Spend and rate are capped** in code, and a batch starts only after a named person
  approves that exact run plan.
- No real money, accounts, credentials or personal data. No private individuals anywhere
  in a graph.
""")

st.caption(f"Pre-registration hash `{prereg_hash()}` — the analysis plan was committed "
           "to git before the main runs, and its hash is stored with every result.")
