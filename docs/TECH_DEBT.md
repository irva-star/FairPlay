# FairPlay — code audit and technical debt

Audited commit `d3d473e` (Oct 23, 23:56), the repo's only commit, on Sun Oct 25 2026.
Method: read every module against `docs/SPEC.md`, ran the full test suite, and
reproduced each finding below with a script or a test. Nothing in the code was
changed; fixes wait for your pick (see "Fix batches" at the end).

## Status update (Oct 25, 11:00)

Fixed in the StreamVault change set, each with a regression test (394 tests pass):
C1 YAML booleans, C2 offer after level 9, C3 retries merging attempts, M4 duplicate
results, M5 lenient screen parsing, L6 zero-variance verdict, both test bugs, and
the unpinned Streamlit from H4 (it broke against FastAPI's Starlette pin). A
correction: this report first said "52 pass"; the real count was 349 pass, 4 fail.
Still open: H1, H2, H3, H5 (Door Run only; StreamVault scopes its oracle by
seed), H6, M1–M3, M6–M10, L1–L5, and all structural debt.

## Summary

The code is well structured and the safety design (S1–S15) is implemented with
care: the guard, the observation schema, the fixed tool list, redaction, the
hash chain and the approval gate are all real and tested. **But three bugs
would make today's results wrong or empty**, and nothing needed to deploy
exists yet.

| | Status |
|---|---|
| Tests | 349 pass, **4 fail** (2 code bugs, 2 test bugs) |
| Results pipeline | **Broken**: `python -m stats` computes no contrasts for any run (C1) |
| Game data | **Biased**: ~15% of sessions end after 9 levels, more often for spenders (C2) |
| Retried sessions | **Contaminated**: both attempts' events merge into one session (C3) |
| Deployment (T9) | **Not started**: no Dockerfile, compose, `snapshot/`, README or CI |
| Spec | `docs/SPEC.md` matched the doc text; now revised alongside this report |

## Build status against the spec's tasks

| Task | State | Notes |
|---|---|---|
| T0 Scaffold and guardrails | Done | S6 scanner present and passing |
| T1 Game | Done, with bug | C2; `levels-played` text is wrong (L4) |
| T2 Calibration | Done | `stats/calibrate.py` |
| T3 Guard, observation, agent loop | Done, with bug | `"level "` accepted as a screen (M5) |
| T4 Model call, budget, limiter, CLI | Done, with bugs | C1, C3; dev and run spend share one cap (M3) |
| T5 Evidence store | Done | Single-writer rule broken by screenshots (M6) |
| T6 Statistics | Done, unreachable | Correct maths, but C1 means it never runs on real data |
| T7 Dashboard | Done | Public mode untested without a snapshot |
| T8 ContextGraph | Done | All edges still have empty `reviewed_by` (acceptance box unticked) |
| T9 Snapshot and hosting | **Export script only** | No Dockerfiles; snapshot leaks names (H1) |
| T10 Validation page | Done, with debt | Oracle not scoped to a run (H5) |
| T11 Sign-off | Done | |
| T12 Evidence pack, LLM summary | Pack done; summary not found | P2 |

`prereg.yaml` still holds placeholder prompt hashes, so it has not yet been
frozen for the main runs.

## Critical (results are wrong or empty)

### C1. YAML turns `on`/`off` into booleans, so no contrast is ever analysed
PyYAML follows YAML 1.1, where bare `on` and `off` are `True` and `False`.

- `configs/run_on.yaml` has `label: on`, so `walker/cli.py:144` stores the
  label as `"True"`; `run_off.yaml` becomes `"False"`. Run IDs read
  `True-1a2b3c4d`.
- `prereg.yaml` has `batch: on` / `batch: off`, which load as `True`/`False`.
- `stats/__main__.py:460-464` compares those against the strings `"on"`/`"off"`.

Reproduced: `contrasts_for()` returns an empty dict for labels `True`, `False`
and `pilot`. Every `python -m stats <run>` prints nothing, writes no results,
and the dashboard's Results and Validation pages stay empty.

**Fix:** quote the values (`label: "on"`, `batch: "on"`), and choose contrasts
from the run's recorded switch rather than from its label. Add a test that
loads the real config files.

### C2. An offer after level 9 ends the session one level early
`game/server.py:211-217` advances `level_no` to 10 *before* the offer is
dismissed. `offer_dismiss` (`:234-236`) then sees `level_no >= 10` and marks
the session done, so level 10 is never played.

Reproduced: 76 of 500 simulated sessions (15%) ended after 9 levels. Offers are
shown with probability 0.8 to targeted spenders and 0.3 to everyone else, so
the truncation is **itself correlated with the planted rule**. M3 (win rate)
divides by a fixed 10 (`stats/metrics.py:97`), so it is biased downward for
spenders by a mechanism that isn't the one being measured. This is also the
failing `test_a_full_session_runs_ten_levels...`.

**Fix:** track "awaiting dismissal" separately from `level_no`, or mark done
only when the dismissed offer came from level 10. Compute M3 over levels
actually resolved.

### C3. A retried session appends its second attempt to the first's evidence
On failure, `walker/cli.py:283-285` retries under the **same** `session_id`.
`start_session` does `INSERT OR REPLACE` (`evidence/store.py:140-147`), but the
events of attempt 1 stay in the table, and the store's in-memory chain and
sequence counters (`store.py:160-161`) keep counting. Attempt 2's events are
appended to attempt 1's chain.

Effects: `stats/metrics.py` counts both attempts' wins, losses and offers
together; M3 can exceed 1.0; attrition is understated. The chain still
verifies, so `verify` won't catch it.

**Fix:** use a per-attempt ID (`…-a2`), mark the failed attempt `status=failed`,
and have `run_metrics` take only the final attempt.

## High

**H1. The public snapshot publishes approvers' full names and the spend log.**
`scripts/export_snapshot.py:217` has `DROP_TABLES = ()`. The `approvals` table
holds the names typed into `walker approve --name "Full Name"`, and the
snapshot is committed to a public repo. Decide: drop the table, or keep
initials only.

**H2. The per-IP rate limit becomes one global limit once hosted.**
`game/server.py:86` keys on `request.client.host`. Behind any host's HTTPS
proxy, that is the proxy's address, so 60 requests per 10 seconds is shared by
every judge scanning the QR code: about two people playing at once. It needs
uvicorn `--proxy-headers` with `--forwarded-allow-ips`, or reading
`X-Forwarded-For`.

**H3. Nothing to deploy.** No Dockerfile, compose file, `.dockerignore`,
healthcheck, `snapshot/` or README. Container design is in the revised
`docs/SPEC.md` section 15.

**H4. One `requirements.txt` for four different runtimes.** The hosted game
needs 2 packages and would install ~15, including Playwright and the anthropic
SDK, which S12 says must never be on the server. `streamlit>=1.37` is
unpinned, against S13. The other pins date from about December 2024
(`anthropic==0.42.0`, `fastapi==0.115.6`) and were "written offline" per the
file's own header.

**H5. V5 scores a run against every oracle line ever written.**
`validation/score_against_oracle.py` reads all of `oracle/ground_truth.jsonl`.
The pilot and the ON batch both run with the switch on and both append there,
so the "truth" for ON mixes in pilot sessions and failed attempts. The oracle
has no run ID to filter on.

**H6. The hosted oracle is written to a file, not to logs.** The spec says
hosted oracle lines go "only to private server logs". The server always opens
`ORACLE_PATH` (default `oracle/ground_truth.jsonl`) and appends, inside the
container's ephemeral disk, with no rotation.

## Medium

- **M1. Unbounded memory on the hosted game.** `PLAYERS` and `_RATE`
  (`game/server.py:72-73`) are never pruned. Every page load adds a player.
  Add a TTL or an LRU cap.
- **M2. The batch switch is declared in two places.** `scripts/run_demo.py`
  `BATCHES` and each config's `switch:` must agree, and nothing checks the
  running server matches. `walker run` alone can mislabel a whole batch.
- **M3. Development and run spend share one ledger.** `total_spend_usd()` sums
  everything, so fake-LLM development counts toward the $10 run cap and vice
  versa. Tag spend rows with a mode.
- **M4. `results` has no key.** `write_result` always inserts, so re-running
  `python -m stats` duplicates every row, and the dashboard query
  (`dashboard/common.py:112-116`) returns all of them.
- **M5. Observation parsing strips whitespace.** `parse_screen("level ")`
  succeeds (`walker/observe.py:76`). The failing
  `test_screen_enum_is_strict[level ]` is right: a strict contract should not
  normalise.
- **M6. Screenshots bypass the single writer.** `walker/writer.py:86` calls
  `store.add_screenshot` directly from session coroutines, while events go
  through the queue. It works today only because sqlite3 calls block the event
  loop; it contradicts SPEC section 7.
- **M7. A crashed writer task goes silent.** If `append_event` raises, the
  drain task dies, later `emit` calls queue forever, and the error only
  surfaces at `close()`.
- **M8. The plan hash ignores `--llm`.** Approving a fake smoke run also
  approves the real run of the same config.
- **M9. Run-level cap checks happen only at session start.** With concurrency
  8, a batch can overshoot by up to 8 sessions' cost. Harmless at these
  prices; worth one line in the spec.
- **M10. Sync file and DB I/O inside async handlers.** The game's oracle
  append and the store's commit-per-event block the loop. Fine at demo scale.

## Low

- **L1.** `game.js:70` sets `levels-played` to `levels_total`, not the count
  played.
- **L2.** `game.js` ignores non-OK responses on `pick` and `dismiss`; a 429
  re-renders the level with `undefined` results. The walker then times out on
  `data-seq` and logs a render timeout instead of the real cause.
- **L3.** The `pid` cookie lacks `Secure`. Set it when served over HTTPS.
- **L4.** Paths are relative to the working directory everywhere
  (`data/`, `snapshot/`, `prereg.yaml`). Containers must use the repo root as
  `WORKDIR`.
- **L5.** `stats/__main__.py` estimates power from the pooled SD across
  personas, which includes the planted effect on ON runs and overstates σ.
- **L6.** Zero variance in both groups with different means returns
  "Inconclusive", even when the difference is obvious.

## Failing tests

| Test | Root cause | Fix in |
|---|---|---|
| `test_budget_guard::test_per_session_cap_is_tracked_separately` | **Test bug.** One step costs $0.00015, under the $0.0002 cap it sets | Test: use a cap of $0.0001 |
| `test_observation_schema::test_screen_enum_is_strict[level ]` | **Code bug** M5 | `walker/observe.py` |
| `test_server::test_a_full_session_runs_ten_levels…` | **Code bug** C2 | `game/server.py` |
| `test_server::test_the_client_has_no_css_animations` | **Test bug.** The CSS comment says "No animations" | Test: match `animation:` and `@keyframes` |

## Structural debt

| Area | Today | Recommended |
|---|---|---|
| Packaging | `requirements.txt` + `conftest.py` path hack | `pyproject.toml` with extras `game`, `dashboard`, `walker`, `dev`; `uv` lockfile |
| Pins | Hand-written offline; one unpinned | Generated lock, reviewed per S13 |
| Python | Spec says 3.11; nothing enforces it | `requires-python = ">=3.11,<3.14"`, one version in the images |
| Lint and types | None | `ruff` (lint + format), `mypy` on `walker/`, `stats/`, `evidence/` |
| CI | None | GitHub Actions: tests, ruff, S6 scan, Docker build |
| Containers | None | Two slim images plus compose (SPEC section 15) |
| Config | YAML 1.1 booleans; switch in two places | Validate configs with a schema at load; switch only in config |
| Schema evolution | `CREATE IF NOT EXISTS` only | A `schema_version` row; unique key on `results` |
| History | One 9k-line commit | Commit `prereg.yaml` and manifest hashes separately, as the spec requires |

## Fix batches

Ordered for a demo today. Each batch is independent and ends with the tests
green.

**A. Make the results correct (about 1 hour; blocks the batches).** C1, C2,
C3, M4, M5, the two test fixes, plus tests that load the real config files.
Re-run the pilot afterwards; earlier evidence has the C2 bias baked in.

**B. Deploy (about 1–2 hours).** Split dependencies, two Dockerfiles,
`docker-compose.yml`, `.dockerignore`, healthcheck route, H1 snapshot
scrubbing, H2 proxy headers, H6 oracle-to-stdout, L3 secure cookie, M1 TTL.

**C. Hardening after the demo.** H5 run-scoped oracle, M2, M3, M6–M10,
`pyproject.toml` and lockfile, ruff/mypy, GitHub Actions, the Low items.
