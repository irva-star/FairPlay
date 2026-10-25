# FairPlay Labs — Build North prototype

LLM agent personas play our own test game, "Door Run", and a statistics engine
checks whether the game treats spenders differently. Our game has a hidden
rule planted in it, so we know the right answer. A second view, ContextGraph,
draws the Entropy money-path graph from a hand-typed YAML file.

The full design is in [docs/SPEC.md](docs/SPEC.md). Known issues and fix plans
are in [docs/TECH_DEBT.md](docs/TECH_DEBT.md).

Every command below runs from the repo root.

---

## 1. Create the Python environment (do this first, once per laptop)

You need **Python 3.11**. Check with `py -3.11 --version` (Windows) or
`python3.11 --version` (macOS/Linux). If it's missing, install 3.11 from
python.org.

**Windows (PowerShell)**
```powershell
py -3.11 -m venv .venv
.venv\Scripts\activate
pip install -r requirements.txt
python -m playwright install chromium
```

**macOS / Linux**
```bash
python3.11 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
python -m playwright install chromium
```

**Every new terminal:** activate the environment again before running anything.
When it's active, your prompt starts with `(.venv)`.
```powershell
.venv\Scripts\activate          # Windows
source .venv/bin/activate       # macOS/Linux
```

**Check that it worked:**
```powershell
pytest -q
```
All tests should pass with **none skipped**. A line like `could not import
'fastapi'` means the install stopped partway. Run `pip install -r
requirements.txt` again and read the first error.

**If the environment is broken,** delete it and start over:
```powershell
Remove-Item -Recurse -Force .venv   # Windows
rm -rf .venv                        # macOS/Linux
```

**API key:** only the laptop that runs real agent batches needs one. Create a
file named `.env` in the repo root containing one line:
```
ANTHROPIC_API_KEY=sk-ant-...
```
`.env` is gitignored. Never commit it, paste it into chat, or put it on the
hosted server.

---

## 2. Play the game locally

```powershell
$env:PLANTED_SWITCH="on"     # or "off"; macOS/Linux: export PLANTED_SWITCH=on
uvicorn game.server:app --host 127.0.0.1 --port 8000 --workers 1
```
Open http://127.0.0.1:8000/?seed=1 and press Ctrl+C to stop the server.

- **Always use `--workers 1`.** Game state lives in memory.
- **The switch is fixed when the server starts.** To change it, restart the
  server.
- **Ground truth:** every level is logged to `oracle/ground_truth.jsonl`.
  Only `validation/` may read it.

---

## 3. Run the agents

Every batch needs a named person to approve its plan before it can run.

### 3a. Smoke test with the fake model ($0)
Start the game as in step 2 with `PLANTED_SWITCH=on`, then in a second
terminal (with the environment activated):
```powershell
python -m walker plan configs/run_pilot.yaml
python -m walker approve <plan_hash> --name "Your Name"
python -m walker run configs/run_pilot.yaml --llm fake
```

### 3b. Real batches
For real batches, stop any game server you started yourself.
`scripts/run_demo.py` starts the server with the right switch, runs the batch
and stops it. Plan and approve each batch first.

| Batch | Config | Sessions | Rough cost |
|---|---|---|---|
| Pilot | `configs/run_pilot.yaml` | 20 | ~$0.05 |
| ON | `configs/run_on.yaml` | 180 | ~$0.50 |
| OFF | `configs/run_off.yaml` | 120 | ~$0.30 |

```powershell
python -m walker plan configs/run_pilot.yaml
python -m walker approve <plan_hash> --name "Your Name"
python scripts/run_demo.py --batch pilot
```
After the pilot, fill in the prompt hashes in `prereg.yaml` from the plan
output, adjust `n` or the margins if the power check says so, and **commit
`prereg.yaml` before the ON and OFF batches**. Then:
```powershell
python -m walker plan configs/run_on.yaml
python -m walker approve <plan_hash> --name "Your Name"
python -m walker plan configs/run_off.yaml
python -m walker approve <plan_hash> --name "Your Name"
python scripts/run_demo.py --batch on --batch off
```
Each run prints its `run_id` at the end. Keep them for step 4.

Spend is capped in code at $10 for runs and $5 for development
(`configs/pricing.yaml`).

---

## 4. Verify, analyse, validate

```powershell
python -m evidence verify <run_id>                          # hash chain + screenshots: OK or first mismatch
python -m stats <run_id>                                    # metrics, CIs, verdicts, power check
python -m validation.score_against_oracle <on_run_id>       # V5: estimates vs ground truth
```
Commit the manifest hashes that `verify` prints before you present results.

---

## 5. Dashboard

**Local mode.** Reads `data/evidence.db` and includes the approvals,
sign-off and live evidence pages:
```powershell
streamlit run dashboard/app.py
```
Open http://127.0.0.1:8501.

**Public mode.** Read-only, reads only `snapshot/`. Build the snapshot
first:
```powershell
python scripts/export_snapshot.py
$env:FAIRPLAY_MODE="public"; streamlit run dashboard/app.py
```
Commit `snapshot/` when you want the hosted dashboard to show new results.

---

## 6. Docker (hosted game and public dashboard)

Two images. Neither contains the agent code, the anthropic SDK, Playwright,
`.env`, `data/` or `oracle/`. **The agents always run on the laptop, never in
Docker.**

Build from the repo root:
```powershell
docker build -f deploy/game.Dockerfile -t fairplay-game .
docker build -f deploy/dashboard.Dockerfile -t fairplay-dashboard .
```
The dashboard image needs a `snapshot/` folder, so run
`python scripts/export_snapshot.py` first.

Run locally. `127.0.0.1:` keeps them off the venue Wi-Fi:
```powershell
docker run --rm -e PORT=8000 -e PLANTED_SWITCH=on -p 127.0.0.1:8000:8000 fairplay-game
docker run --rm -e PORT=8501 -p 127.0.0.1:8501:8501 fairplay-dashboard
```

**On a host:** deploy each image as a container service behind HTTPS.

- **Port:** the host supplies `$PORT`.
- **Game:** set `PLANTED_SWITCH` in the host's environment and run **one
  instance only**. Its ground-truth lines go to the host's private logs.
- **No API key:** never set `ANTHROPIC_API_KEY` on the host. Nothing there
  needs it.
- **Agent allowlist:** after deploying the game, its HTTPS domain replaces
  `REPLACE-WITH-OUR-GAME-HOST` in `walker/guard.py`. That's a code change both
  teammates agree on.
- **Before the demo:** free hosts sleep when idle, so open the game a few
  minutes early.

---

## Troubleshooting

| Symptom | Fix |
|---|---|
| `No module named ...` / tests skipped | Environment not activated, or the install stopped partway. Activate it, then run `pip install -r requirements.txt` again. |
| `py -3.11` not found | Install Python 3.11 from python.org. |
| `REFUSED: no approval for plan` | Run `walker plan` and approve the hash it prints. Any config or prompt change makes a new hash. |
| `REFUSED: ANTHROPIC_API_KEY is not set` | Add it to `.env`, or use `--llm fake`. |
| `label must be a quoted string` / `switch must be "on" or "off"` | Quote the value in the YAML: `switch: "on"`. Bare `on`/`off` are read as true/false. |
| Port 8000 already in use | Another game server is still running. Stop it with Ctrl+C or close its terminal. |
| Docker build fails at `COPY snapshot/` | Run `python scripts/export_snapshot.py` first. |
