# FairPlay Labs

An engine that audits how an app *treats* different users. LLM agents play
controlled personas through an app, every observation is stored as
hash-chained evidence, and statistics decide whether a difference is real.
A human approves every run and every finding.

Two apps to audit, both ours, both with a known hidden rule:

- **StreamVault** (`streamvault/`): a fictional streaming signup with drip
  pricing, pre-ticked add-ons and a cancellation maze. A hidden judge rule
  treats renters worse. The live demo.
- **Door Run** (`game/`): a 10-level door game with a planted switch. The
  calibration benchmark (A/A, permutation calibration, recovery of truth).

Full spec: `docs/SPEC.md` (StreamVault is section 16). Audit and open debt:
`docs/TECH_DEBT.md`.

## Setup

```bash
python -m venv .venv && source .venv/bin/activate      # Windows: .venv\Scripts\activate
pip install -r requirements.txt
python -m playwright install chromium
pytest -q                                               # 394 tests
```

Put `ANTHROPIC_API_KEY=sk-ant-...` in `.env` (gitignored). Only the walker reads it.

## StreamVault demo (blind test)

```bash
# 1. Plan and approve (the plan never contains the hidden rule)
python -m walker plan configs/run_sv_live.yaml
python -m walker approve <plan_hash> --name "Your Name"

# 2. Run: starts StreamVault with a SECRET rule, runs the agents, stops it.
#    Free rehearsal: add --llm fake
python scripts/run_demo.py --batch sv-live --judge-rule secret

# 3. Analyse, then reveal
python -m stats <run_id>
python -m validation.sv_reveal <run_id>

# 4. Dashboard (live Evidence page refreshes every 2 s during a run)
streamlit run dashboard/app.py
```

`<run_id>` is printed at the end of step 2. To let a judge choose the rule,
pass `--judge-rule fee|maze|preticked|none` typed by the judge instead.
If Playwright's browser download is blocked, point the walker at an installed
Chrome: `export CHROMIUM_EXECUTABLE=/path/to/chrome`.

## Spot the trap: four StreamVault scenarios

One StreamVault server can run four scenarios, A to D, each with one secret
rule (extra fee, longer cancel, pre-ticked add-ons, or no trick):

```bash
python -m walker plan configs/run_sv_copy_a.yaml   # repeat plan + approve for b, c, d
python scripts/run_demo.py --batch copies          # rules shuffled and sealed in oracle/judge_rules.secret
python scripts/analyse_copies.py                   # statistics + reveal for all four
python scripts/export_snapshot.py                  # public copy, approver names reduced to initials
```

Hosted, the root page is a "spot the trap" chooser linking to /a/ /b/ /c/ /d/.

## Deploy on Render (no API key on any server)

1. Push the repo (with `snapshot/` committed) to GitHub.
2. Render → New → Blueprint → pick the repo. `render.yaml` creates two services.
3. On the `streamvault` service set `JUDGE_RULES` to the contents of
   `oracle/judge_rules.secret` (don't read it out), and `DASHBOARD_URL` to the
   dashboard's URL once it exists.
4. Open both URLs a minute before anyone looks: free services sleep after 15
   idle minutes and take about a minute to wake.
5. To let the agents reach the hosted copy, put its host into `HOSTED` in
   `walker/guard.py` (a code change by design, rule S1).

## Door Run batches

```bash
python -m walker plan configs/run_on.yaml && python -m walker approve <hash> --name "Your Name"
python scripts/run_demo.py --batch on --batch off          # 60 per persona (pre-registered)
python scripts/run_demo.py --batch on-quick --batch off-quick   # 20 per persona, quick rehearsal
GAME_SKIN=rpg uvicorn game.server:app --port 8000 --workers 1   # same game, "Astral Vault" sci-fi look
python -m stats <run_id>
python -m validation.score_against_oracle <run_id>
```

## Layout

| Folder | Role |
|---|---|
| `game/`, `streamvault/` | The audited apps. Hidden rules live server-side only |
| `walker/` | Agents: guard, typed observations, fixed tools, budget, rate limit, CLI |
| `walker/apps/` | One adapter per audited app |
| `evidence/` | SQLite evidence store, hash chain, manifests, `verify` |
| `stats/` | Metrics, Welch CI, TOST, Holm, verdicts, power |
| `validation/` | The only code that reads `oracle/`: scores results against truth |
| `dashboard/` | Streamlit, local (approvals) and public (read-only snapshot) modes |
