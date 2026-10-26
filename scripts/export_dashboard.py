"""Turn the public snapshot into the JSON the hosted dashboard draws from.

    python scripts/export_dashboard.py

Reads snapshot/evidence.db (read-only) and snapshot/validation/*.json, and
writes hub/static/dashboard.json. Only the newest complete run per label
is kept, so a real batch replaces an earlier rehearsal of the same label. Runs
whose agent steps came from the fake model are flagged "rehearsal" so the page
never passes stub reasoning off as Claude's.

Like export_snapshot.py, this never reads data/ or oracle/: the snapshot is
already the public copy, with approver names reduced to initials.
"""

from __future__ import annotations

import argparse
import json
import sqlite3
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path
from statistics import mean

import yaml

DEFAULT_DB = Path("snapshot/evidence.db")
DEFAULT_VALIDATION = Path("snapshot/validation")
DEFAULT_OUT = Path("hub/static/dashboard.json")
REASON_CHARS = 220

# The agent's own words are the only free text that leaves the snapshot; every
# observation field below is an integer or a fixed enum (SPEC section 6).
SV_FIELDS = ("screen", "headline_price", "cancel_step")
DOOR_FIELDS = ("screen", "level", "gems", "offer_price", "last_result")


def _metric_names() -> dict[str, dict[str, str]]:
    door = yaml.safe_load(Path("prereg.yaml").read_text(encoding="utf-8"))["metrics"]
    sv = yaml.safe_load(Path("prereg_streamvault.yaml").read_text(encoding="utf-8"))["metrics"]
    return {
        "doorrun": {k: v["description"] for k, v in door.items()},
        "streamvault": {k: v["name"] for k, v in sv.items()},
    }


def _personas() -> dict[str, str]:
    raw = yaml.safe_load(Path("configs/personas.yaml").read_text(encoding="utf-8"))["personas"]
    return {name: p["sentence"] for name, p in raw.items()}


def _step(payload: dict, app: str) -> dict:
    obs = payload.get("observation") or {}
    keep = {k: obs.get(k) for k in (SV_FIELDS if app == "streamvault" else DOOR_FIELDS)}
    if app == "doorrun" and obs.get("doors"):
        keep["doors"] = [d.get("win_chance") for d in obs["doors"]]
    if app == "streamvault" and obs.get("checkout"):
        keep["total"] = obs["checkout"].get("total")
    args = {k: v for k, v in (payload.get("args") or {}).items() if k != "reason"}
    return {
        "tool": payload.get("tool"),
        "args": args,
        "reason": (payload.get("reason") or "")[:REASON_CHARS],
        "obs": keep,
        "rejected": payload.get("rejected"),
    }


def _latest_runs(conn: sqlite3.Connection) -> list[sqlite3.Row]:
    newest: dict[str, sqlite3.Row] = {}
    for row in conn.execute("SELECT * FROM runs WHERE status='complete' ORDER BY created_at"):
        newest[row["label"]] = row
    return sorted(newest.values(), key=lambda r: r["label"])


def _validation(folder: Path) -> dict[str, dict]:
    found: dict[str, dict] = {}
    for path in sorted(folder.glob("*.json")) if folder.exists() else []:
        doc = json.loads(path.read_text(encoding="utf-8"))
        if "run_id" in doc:
            found[doc["run_id"]] = doc
    return found


def export(db: Path, validation_dir: Path) -> dict:
    conn = sqlite3.connect(f"file:{db.as_posix()}?mode=ro", uri=True)
    conn.row_factory = sqlite3.Row
    names = _metric_names()
    validation = _validation(validation_dir)
    apps: dict[str, list[dict]] = defaultdict(list)
    totals = {"agents": 0, "steps": 0, "runs": 0, "usd": 0.0}

    for run in _latest_runs(conn):
        app = run["app"] or "doorrun"
        sessions = conn.execute(
            "SELECT * FROM sessions WHERE run_id=? ORDER BY persona, seed", (run["run_id"],)
        ).fetchall()
        models: set[str] = set()
        out_sessions = []
        for s in sessions:
            steps, outcome = [], {}
            for ev in conn.execute(
                "SELECT type, payload FROM events WHERE session_id=? ORDER BY seq", (s["session_id"],)
            ):
                payload = json.loads(ev["payload"] or "{}")
                if ev["type"] == "agent_step":
                    models.add(payload.get("model") or "unknown")
                    steps.append(_step(payload, app))
                elif ev["type"] in ("checkout_seen", "cancelled", "session_end"):
                    outcome[ev["type"]] = {k: v for k, v in payload.items() if k != "notes"}
                elif ev["type"] == "level_end":
                    outcome.setdefault("levels", []).append(payload.get("result"))
                elif ev["type"] == "offer_shown":
                    outcome["offers"] = outcome.get("offers", 0) + 1
            metrics = {
                r["metric"]: r["value"]
                for r in conn.execute("SELECT metric, value FROM metrics WHERE session_id=?",
                                      (s["session_id"],))
            }
            out_sessions.append({
                "id": s["session_id"], "persona": s["persona"], "seed": s["seed"],
                "status": s["status"], "steps": steps, "outcome": outcome, "metrics": metrics,
            })

        by_persona: dict[str, dict[str, list[float]]] = defaultdict(lambda: defaultdict(list))
        for s in out_sessions:
            for metric, value in s["metrics"].items():
                if value is not None:
                    by_persona[s["persona"]][metric].append(value)
        persona_means = {
            p: {m: round(mean(vs), 3) for m, vs in ms.items()} for p, ms in by_persona.items()
        }
        results = [
            {k: r[k] for k in ("contrast", "metric", "n_a", "n_b", "diff", "ci_low", "ci_high",
                               "p_holm", "margin", "verdict")}
            for r in conn.execute(
                "SELECT * FROM results WHERE run_id=? ORDER BY contrast, metric", (run["run_id"],))
        ]
        usd = conn.execute("SELECT COALESCE(SUM(usd), 0) FROM spend WHERE run_id=?",
                           (run["run_id"],)).fetchone()[0]
        real_models = sorted(m for m in models if m != "fake")
        apps[app].append({
            "run_id": run["run_id"], "label": run["label"], "created_at": run["created_at"],
            "switch": run["switch"], "rehearsal": not real_models,
            "model": real_models[0] if real_models else "fake",
            "usd": round(usd, 4), "persona_means": persona_means, "results": results,
            "validation": validation.get(run["run_id"]), "sessions": out_sessions,
        })
        totals["runs"] += 1
        totals["agents"] += len(out_sessions)
        totals["steps"] += sum(len(s["steps"]) for s in out_sessions)
        totals["usd"] = round(totals["usd"] + usd, 4)

    conn.close()
    return {
        "generated_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "banner": "TEST GAME, planted rule (demo)",
        "metric_names": names, "personas": _personas(), "totals": totals, "apps": apps,
    }


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--db", type=Path, default=DEFAULT_DB)
    ap.add_argument("--validation", type=Path, default=DEFAULT_VALIDATION)
    ap.add_argument("--out", type=Path, default=DEFAULT_OUT)
    args = ap.parse_args()
    doc = export(args.db, args.validation)
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(json.dumps(doc, separators=(",", ":")), encoding="utf-8")
    t = doc["totals"]
    print(f"wrote {args.out}: {t['runs']} runs, {t['agents']} agents, {t['steps']} steps")


if __name__ == "__main__":
    main()
