"""Regression tests for the bugs found in the Oct 25 audit (docs/TECH_DEBT.md)."""

from __future__ import annotations

from pathlib import Path

import pytest
import yaml

from evidence.store import EvidenceStore
from stats import metrics as M
from stats.__main__ import contrasts_for, load_prereg
from walker.cli import build_plan, on_off


# --- C1: YAML 1.1 read bare on/off as booleans ------------------------------

@pytest.mark.parametrize("name, switch", [("pilot", "on"), ("on", "on"), ("off", "off")])
def test_real_batch_configs_load_with_string_labels(name: str, switch: str) -> None:
    plan = build_plan(f"configs/run_{name}.yaml")
    assert plan.label == name
    assert plan.switch == switch


def test_every_prereg_contrast_is_reachable() -> None:
    prereg = load_prereg()
    on, off = contrasts_for("on", prereg), contrasts_for("off", prereg)
    assert set(on) == {"V1_sensitivity", "V3_aa"}
    assert set(off) == {"V2_specificity"}


def test_a_boolean_label_is_refused(tmp_path: Path) -> None:
    cfg = yaml.safe_load(Path("configs/run_on.yaml").read_text())
    cfg["label"] = True
    path = tmp_path / "bad.yaml"
    path.write_text(yaml.safe_dump(cfg))
    with pytest.raises(ValueError, match="quote it"):
        build_plan(path)


@pytest.mark.parametrize("raw, expected", [(True, "on"), ("on", "on"), (False, "off"), ("OFF", "off")])
def test_on_off_normalises(raw, expected) -> None:
    assert on_off(raw) == expected


# --- C3: a retry must not merge into the failed attempt ----------------------

def _session(store: EvidenceStore, sid: str, *, attempt: int, results: list[str]) -> None:
    store.start_session(sid, run_id="r1", persona="free", seed=7, attempts=attempt)
    for level, result in enumerate(results, start=1):
        store.append_event(sid, type="level_end", payload={
            "level": level, "result": result, "door": 0,
            "chosen_chance": 60, "best_chance": 60, "chose_best": True})
    store.end_session(sid, status="ok")


def test_only_the_final_attempt_enters_the_analysis(tmp_path: Path) -> None:
    store = EvidenceStore(tmp_path / "e.db")
    store.create_run("r1", label="on", switch="on")
    _session(store, "r1-free-7-a1", attempt=1, results=["LOSS", "LOSS"])
    _session(store, "r1-free-7-a2", attempt=2, results=["WIN"] * 10)

    rows = M.run_metrics(store.conn, "r1")
    assert [r.session_id for r in rows] == ["r1-free-7-a2"]
    assert rows[0].m3 == 1.0


# --- M4: re-running the analysis must not duplicate results ------------------

def test_results_are_replaced_not_duplicated(tmp_path: Path) -> None:
    store = EvidenceStore(tmp_path / "e.db")
    row = {"contrast": "V1_sensitivity", "metric": "M1", "n_a": 1, "n_b": 1,
           "diff": 0.5, "ci_low": 0.1, "ci_high": 0.9}
    store.write_result("r1", row)
    store.write_result("r1", {**row, "diff": 0.4})
    got = store.conn.execute("SELECT diff FROM results WHERE run_id='r1'").fetchall()
    assert [r[0] for r in got] == [0.4]


def test_old_databases_gain_the_switch_column(tmp_path: Path) -> None:
    import sqlite3

    path = tmp_path / "old.db"
    conn = sqlite3.connect(path)
    conn.execute("CREATE TABLE runs(run_id TEXT PRIMARY KEY, label TEXT, created_at TEXT,"
                 " config_hash TEXT, plan_hash TEXT, approval_id INTEGER, git_commit TEXT,"
                 " status TEXT)")
    conn.commit()
    conn.close()
    store = EvidenceStore(path)
    cols = {r[1] for r in store.conn.execute("PRAGMA table_info(runs)")}
    assert {"switch", "app"} <= cols
