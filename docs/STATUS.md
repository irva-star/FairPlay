# FairPlay — current status and next steps

Companion docs: [README.md](../README.md) is *how* to run things,
[SPEC.md](SPEC.md) is the design, [TECH_DEBT.md](TECH_DEBT.md) is the code audit.
Read TECH_DEBT with §2 below beside it — its Batch A is now applied, so several
of its findings are already closed.

---

## 0. One thing blocks everything, and it isn't code

**No Python environment exists on this machine.** There is no `.venv`, and none
of the 16 dependencies are importable — not `pytest`, not `fastapi`, not
`numpy`. `data/`, `oracle/` and `snapshot/` are all empty, so no run has ever
happened here.

**Nothing is runnable until step 1 of §6.** That is an environment gap, not a
missing feature. Every component below is written; none of it has been
executed on this laptop.

---

## 1. Can we run the game right now?

**The game is finished — it is not a missing piece.** Once the environment
exists you can play it by hand in a browser in about a minute:

```powershell
$env:PLANTED_SWITCH="on"
uvicorn game.server:app --host 127.0.0.1 --port 8000 --workers 1
# open http://127.0.0.1:8000/?seed=1
```

Ten levels, three doors each, a 100-gem cosmetic in the store. Buy the Gold
frame and you become a "spender"; with the switch on, your door odds drop 15
points after a loss (70% of the time) and you get offered a retry far more
often (80% vs 30%). Every level is appended to `oracle/ground_truth.jsonl`.

Agents can also play it for **$0** with `--llm fake` (§6 step 5), which
exercises the guard, the agent loop and the evidence store without an API key
or a single paid call.

What you **cannot** do yet:

| | Why |
|---|---|
| Play the **hosted** copy | Images build, but nothing is deployed |
| Point agents at the hosted copy | `walker/guard.py:33` is still `REPLACE-WITH-OUR-GAME-HOST` |
| See results on the dashboard | No run has produced data yet |
| View the **public** dashboard | `snapshot/` is empty, so there is nothing to read |
| Build the dashboard image | Its `COPY snapshot/` fails on an empty folder |

---

## 2. What changed in the 5 commits just pulled

The audit ([TECH_DEBT.md](TECH_DEBT.md)) found three bugs that would have made
the results **wrong or empty**. All three are fixed, and each now has a
regression test.

| Audit item | What was wrong | Fix |
|---|---|---|
| **C1** | PyYAML reads bare `on`/`off` as booleans, so the ON batch's label became `"True"`, no contrast ever matched, and *every* analysis silently produced nothing | Values quoted; `runs` gained a `switch` column; contrasts now chosen from the run's recorded switch, and both `build_plan` and `contrasts_for` raise on an unquoted value |
| **C2** | An offer after level 9 ended the session a level early — and because offers fire at 0.8 for targeted spenders vs 0.3 otherwise, **the truncation was itself correlated with the planted rule** | End-of-session keys off `levels_resolved`, not `level_no`; M3 is now computed over levels actually resolved (`stats/metrics.py:98`) and `prereg.yaml` restates M3 to match |
| **C3** | A retried session appended its second attempt onto the first's hash chain, so metrics double-counted and M3 could exceed 1.0 | Per-attempt session IDs (`…-a2`); `run_metrics` selects only the final attempt per (persona, seed) |
| **M4** | Re-running the analysis duplicated every results row | `INSERT OR REPLACE`, plus a unique index on `(run_id, contrast, metric)` created after older rows are de-duplicated |
| **M5** | `"level "` was accepted as a screen | Enums compared exactly, no whitespace normalising |
| **H2** | The per-IP rate limit collapsed into one global limit behind a host's proxy | `--proxy-headers --forwarded-allow-ips` in the game image |
| **H6** | The hosted oracle was written to a file | `ORACLE_PATH=/dev/stdout`, so lines go to the host's private logs |

Also added:

- **`deploy/game.Dockerfile`** — installs only `fastapi` and `uvicorn`
  (grepped out of the one reviewed `requirements.txt`, so S13 still holds),
  copies only `game/`, runs as a non-root user, one worker.
- **`deploy/dashboard.Dockerfile`** — public mode, installs no `anthropic` and
  no `playwright`, and copies neither `walker/` nor `evidence/` nor `data/`.
  The run/approve/export code paths cannot even load.
- **`.dockerignore`** excluding `.env`, `.env.*`, `data/`, `oracle/`.
- **`tests/test_configs.py`** — loads the *real committed* config files rather
  than hand-written dicts, which is what makes C1 unable to come back quietly.
- **`tests/test_deploy.py`** — asserts each image copies only its own folders,
  names no API key, installs neither `anthropic` nor `playwright`, and that
  `.dockerignore` excludes the secrets and private data.
- **`README.md`** (full setup and run guide) and **SPEC §15** (container design).

---

## 3. What is implemented

About 9,050 lines: 5,178 of application code across 41 files, 3,224 of tests
(227 test functions in 18 files), and 653 of game UI, SQL, config and
Dockerfiles. Spec tasks T0–T8 done, T9 mostly done, T10–T11 done, T12 partial.

### Game — `game/`
- `rules.py` is pure and seeded, imported by both the server and the offline
  simulator so they cannot drift. Three named streams per level
  (`{seed}:{level}:doors|outcome|planted`), and the planted stream always draws
  all three of its values in a fixed order, so a level's rolls don't depend on
  the path the player took to reach it.
- The planted rule fires only for a spender while the switch is on: all three
  doors −15 points with probability 0.7 on the level *after* a loss, and offers
  at 0.8 instead of 0.3. **Offer price is drawn identically either way** — that
  is the negative control (M4) that shows the detector isn't just flagging
  everything.
- `server.py` is FastAPI, in-memory state per `pid` cookie, one worker. The
  switch is read from the environment once at import, so no route can change a
  running server's condition. Repeat purchase returns 409. No `/docs`, no admin
  routes.
- The page exposes a narrow DOM contract: digits-only numeric nodes, a
  three-value `last-result`, and `#game[data-seq]`, a render counter the client
  bumps after each paint. The walker waits on `data-seq` because `data-state`
  stays `"level"` from one level to the next and so cannot signal a new level.

### Agent walker — `walker/`
- `guard.py` — exactly two allowed origins, enforced twice: request
  interception aborts anything else, and Chromium launches with a host-resolver
  rule that fails DNS for every other host (which also covers WebSockets, which
  `context.route` does not intercept). Scheme and host compare
  case-insensitively.
- `observe.py` — the S2 boundary. Strict parsers refuse anything that isn't
  digits or a known enum, **and the error message never echoes the offending
  text**, so a compromised page aborts the session instead of getting
  paraphrased to the model. A JSON schema with `additionalProperties: false` is
  the structural backstop. `recent` entries must match a fixed pattern, and the
  single retry carries one of three fixed error strings — so a retry can never
  become a channel for free text.
- Five tools, fixed order, no navigation/typing/URL/file/shell tool. There is
  deliberately **no accept-offer tool**, so play stays comparable between
  personas. `reason` is capped at 120 chars; unexpected arguments are dropped.
- `agent.py` — one invalid call (refusal, `max_tokens`, no `tool_use` block, or
  an out-of-contract call) gets one retry; a second ends the session as
  `protocol_deviation`. Emits semantic events (`level_end`, `offer_shown`,
  `purchase`, …) so the stats layer never has to replay observations.
- `llm.py` is the only file importing `anthropic`. Forced `tool_choice`, no
  temperature/top_p/top_k, no prefill, no thinking, content blocks selected by
  type rather than position. Reads rate-limit headers off the raw response to
  feed the limiter.
- `budget.py` computes cost from each response's own four usage fields rather
  than an estimate. `ratelimit.py` paces to 80% of the reported limits, falls
  back to 40 req/min, honours `retry-after` (clamped to 300s).
- `cli.py` — `plan` / `approve` / `run`. The plan hash covers origin, switch,
  personas, model and prompt hashes, so changing any of them invalidates the
  approval instead of silently reusing it. Loads `.env` with ~15 lines of
  stdlib rather than adding a dependency; real environment variables win.

### Evidence — `evidence/`
SQLite in WAL mode so the dashboard can read a live run. One writer task fed by
an `asyncio.Queue`. `row_hash = sha256(prev_hash + canonical_json(event))`,
chain starting at `sha256(session_id)`; manifests record every chain's final
hash plus every screenshot's. Credentials are redacted on the way in, so
nothing downstream can leak one. This is tamper-**evident**, not tamper-proof —
committing each manifest hash to git before analysis is what closes the gap.

### Statistics — `stats/`
Reads evidence only, never the oracle (enforced by a test). Welch difference
with Satterthwaite df, and TOST equivalence computed from *the same* statistics,
so the two tests cannot disagree about the data underneath them. Hedges' g,
Holm across M1–M4 (B1 sits outside the family on purpose), bootstrap CI as a
robustness check. The four-way verdict exists so **"we found nothing" is never
reported as "there is nothing"**; degenerate input returns `Inconclusive`
rather than letting NaN reach the dashboard.

### Validation, ContextGraph, dashboard
- `validation/` is the only module permitted to read `oracle/`, and it writes a
  small JSON so the dashboard never needs to.
- `contextgraph/` loads one hand-typed YAML file. The schema has **no `Person`
  kind at all**, which is how S8 holds; labels and claims are length-capped.
  The analysis intersects every donor→streamer money path (never the `unknown`
  edges) and flags regulated chokepoints as actionable.
- `dashboard/` has 7 pages in two modes. Pages are registered
  programmatically, so in public mode the approvals and sign-off pages are
  *never constructed* — a page that does not exist cannot be reached.

---

## 4. What is missing

### Blocking a demo
1. **No environment** (§0). Nothing runs.
2. **`prereg.yaml` prompt hashes are still placeholders.** Fill them from
   `walker plan` output and **commit before the ON and OFF batches** — that
   commit is what makes the analysis plan checkable after the fact.
3. **No run has ever happened.** No real API call has been made, so
   prompt-cache behaviour is unconfirmed: check
   `usage.cache_read_input_tokens` after the first two real calls. If the
   cached prefix is under 512 tokens, caching silently does not apply.
4. **`snapshot/` is empty**, so the public dashboard has nothing to read and
   the dashboard image cannot build.

### Blocking hosting
5. **`walker/guard.py:33` is still `REPLACE-WITH-OUR-GAME-HOST`.** Agents
   cannot touch the hosted game until the real domain replaces it. By design
   that is a code change both teammates agree to, not a config flag.
6. **No `docker-compose.yml`, no healthcheck route, no CI.** Both images build
   and run individually.

### Correctness and safety still open (from TECH_DEBT, not yet fixed)
7. **H1 — the public snapshot would publish approvers' full names and the whole
   spend log.** `scripts/export_snapshot.py:26` is still `DROP_TABLES = ()`,
   and the `approvals` table holds whatever was typed into
   `walker approve --name`. This repo is public. **Decide before the first
   `export_snapshot` run:** drop the table, or store initials only.
8. **H5 — V5 scores a run against every oracle line ever written.** The pilot
   and the ON batch both run with the switch on and both append to the same
   file, so ON's "truth" mixes in pilot sessions. The oracle has no run ID to
   filter on.
9. **M1 — `PLAYERS` and `_RATE` are never pruned** (`game/server.py:73-74`).
   Every page load adds a player permanently; a crowd scanning the QR code is
   the likeliest way the hosted game falls over.
10. **L3 — the `pid` cookie has no `Secure` flag** (`game/server.py:145`).
11. **ContextGraph: all 9 edges still have `reviewed_by: ""`.** They render as
    faded drafts, and the spec's acceptance box stays unticked until the curator
    confirms each claim against the CBC article.
12. **T12's LLM plain-language summary** is not implemented (it is P2).

### Known non-blocking debt
M2, M3, M6–M10 and L1–L6 in [TECH_DEBT.md](TECH_DEBT.md), plus the structural
items (no `pyproject.toml`, no lockfile, no ruff/mypy, one 9k-line commit).

---

## 5. What has not been verified

**The test suite has never been run on this machine** — no dependencies are
installed. The audit found 52 pass / 4 fail at the first commit; Batch A fixed
all four root causes and added `test_configs.py` and `test_deploy.py`, and the
README asserts the suite is green. **Treat `pytest -q` as the first real gate,
not a formality.**

Nothing requiring the uninstalled packages has been exercised: the stats
layer's scipy/statsmodels cross-checks, the FastAPI server tests, the networkx
chokepoint analysis, the Streamlit dashboard, the jsonschema structural check,
and both Docker builds.

---

## 6. What to run next, in order

Each step is a gate: if it fails, stop there rather than pressing on.

| # | Command | Gate |
|---|---|---|
| 1 | `py -3.11 -m venv .venv` → activate → `pip install -r requirements.txt` → `python -m playwright install chromium` | README §1 |
| 2 | `pytest -q` | **All pass, none skipped.** A skip means the install stopped partway |
| 3 | `python -m stats.calibrate` | Win rate 60–68%; note the SDs for the power check |
| 4 | Start the game (§1), play `?seed=1` by hand | Ten levels; buy the frame and feel the rule |
| 5 | `walker plan configs/run_pilot.yaml` → `walker approve <hash> --name "…"` → `walker run configs/run_pilot.yaml --llm fake` | A full batch at **$0**. Proves guard, loop, evidence |
| 6 | `python -m evidence verify <run_id>` | `OK`, every chain and screenshot |
| 7 | `python -m stats <run_id>` | **The C1 regression check.** It must print contrasts and write rows. Empty output means C1 is back |
| 8 | Real run, 2 sessions | Spend lands in the `spend` table; **check `cache_read_input_tokens`** |
| 9 | Real pilot (20 sessions, ~$0.05), then the power check | ≥95% compliance. If a primary metric needs >60 sessions, change `n` or the margin |
| 10 | Fill the prompt hashes in `prereg.yaml`, **commit it** | Must precede step 11 |
| 11 | `python scripts/run_demo.py --batch on --batch off` | ~2h at entry-tier limits, ~$0.80 |
| 12 | `verify`, `python -m stats`, `python -m validation.score_against_oracle <on_run_id>` | Commit the manifest hashes before presenting |
| 13 | **Decide H1**, then `python scripts/export_snapshot.py` | Do not publish approver names by accident |
| 14 | Dashboard local, then public mode | Results, Evidence, Validation, ContextGraph all populate |
| 15 | Build both images, deploy, then update `guard.py` with the real host | Both teammates sign off on the guard change |

Steps 1–7 cost nothing and need no API key. **Step 7 is the most important
early check**, because C1 was the bug that silently produced empty results —
and an empty results table looks identical to "no effect found".

---

## 7. What to test

### Safety — these gate every run (spec §12)
- A request to a non-allowlisted host is aborted and logged; a public URL in a
  config refuses to start (S1).
- The observation schema rejects extra fields, unknown enums and free text, and
  an injected label never reaches the model (S2, S4).
- The executor rejects out-of-list and out-of-range calls; no navigation or
  typing tool exists (S3).
- No network library is imported in app folders outside `walker/llm.py`, and
  `stats/` never opens `oracle/` (S6).
- The budget guard stops a run at its cap; `plan` refuses when the estimate
  exceeds what is left (S5).
- The limiter honours `retry-after` on a mocked 429 and falls back to 40
  req/min when headers are missing.
- Neither image carries an API key, agent code or private data (S12,
  `test_deploy.py`), and public mode shows no run, approve or export control.
- Every ContextGraph edge has `reviewed_by` filled; no person is named (S8, S9).
- `/permissions` in Claude Code lists every rule from `.claude/settings.json`
  (S15).

### Pipeline
Repeat purchase → 409; `data-seq` rises after every response; a fake-LLM
session completes at $0; refusal / `max_tokens` / missing `tool_use` all
handled as invalid calls; two real sessions recorded with cache reads checked;
≥95% pilot compliance; four concurrent sessions with no "database is locked"; a
flipped byte fails `verify` **and names the file**; total spend ≤ $10 and shown
on the dashboard.

### Method — the five checks that make the engine worth trusting
| | Passes when |
|---|---|
| **V1** sensitivity | M1 and M2 detected; M4 and B1 equivalent |
| **V2** specificity | Switch off: M1, M2, M4 equivalent, nothing detected |
| **V3** A/A | `free_twin` vs `free`: nothing detected |
| **V4** calibration | ≤7% of 1,000 random splits raise any alarm (≈5% expected) |
| **V5** recovery | Oracle truth inside the 95% CI for M1 and M2 |

V2 and V3 matter more than V1. A detector that always said "difference" would
find our planted rule too.

### Demo
Hosted game and dashboard load on a phone over mobile data after a wake-up
visit; the local dashboard shows a live run; a backup video is recorded.

---

## 8. Suggested split

Mirrors the spec's ownership. Steps 1–2 are shared and come first.

- **Person A** — steps 4, 5, 8, 9, 11 (game, agents, batches); then H1's
  decision, the deploy, and the `guard.py` host change.
- **Person B** — steps 6, 7, 12, 14 (evidence, stats, validation, dashboard);
  then M1/L3, H5's run-scoped oracle, and the ContextGraph `reviewed_by` pass
  against the CBC article.
