"""SQLite evidence store (SPEC section 7).

WAL mode, so the local dashboard can read the live run while the walker writes.
During a run exactly one writer task touches this file, and each session's
events arrive in order from a single coroutine, so the hash chain needs no
locking.
"""

from __future__ import annotations

import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from evidence import integrity

SCHEMA_PATH = Path(__file__).parent / "schema.sql"
DEFAULT_DB = Path("data/evidence.db")


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def connect(path: str | Path = DEFAULT_DB, *, read_only: bool = False) -> sqlite3.Connection:
    path = Path(path)
    if read_only:
        conn = sqlite3.connect(f"file:{path.as_posix()}?mode=ro", uri=True)
    else:
        path.parent.mkdir(parents=True, exist_ok=True)
        conn = sqlite3.connect(path, timeout=5.0)
        conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA busy_timeout=5000")
    conn.row_factory = sqlite3.Row
    return conn


def _migrate(conn: sqlite3.Connection) -> None:
    """Add columns introduced after the first release to an existing database."""
    cols = {r[1] for r in conn.execute("PRAGMA table_info(runs)")}
    for name in ("switch", "app"):
        if name not in cols:
            conn.execute(f"ALTER TABLE runs ADD COLUMN {name} TEXT")


class EvidenceStore:
    def __init__(self, path: str | Path = DEFAULT_DB, *, read_only: bool = False) -> None:
        self.path = Path(path)
        self.read_only = read_only
        self.conn = connect(path, read_only=read_only)
        if not read_only:
            self.conn.executescript(SCHEMA_PATH.read_text(encoding="utf-8"))
            _migrate(self.conn)
            self.conn.commit()
        self._chain: dict[str, str] = {}
        self._seq: dict[str, int] = {}

    def close(self) -> None:
        self.conn.close()

    def __enter__(self) -> "EvidenceStore":
        return self

    def __exit__(self, *exc) -> None:
        self.close()

    # --- runs, approvals, sessions -------------------------------------------

    def create_run(self, run_id: str, *, label: str, config_hash: str = "",
                   plan_hash: str = "", approval_id: int | None = None,
                   git_commit: str = "", status: str = "running",
                   switch: str | None = None, app: str = "doorrun") -> None:
        self.conn.execute(
            "INSERT OR REPLACE INTO runs(run_id,label,created_at,config_hash,plan_hash,"
            "approval_id,git_commit,status,switch,app) VALUES(?,?,?,?,?,?,?,?,?,?)",
            (run_id, label, _now(), config_hash, plan_hash, approval_id, git_commit, status,
             switch, app),
        )
        self.conn.commit()

    def set_run_status(self, run_id: str, status: str) -> None:
        self.conn.execute("UPDATE runs SET status=? WHERE run_id=?", (status, run_id))
        self.conn.commit()

    def record_approval(self, *, kind: str, object_hash: str, approver: str,
                        note: str = "") -> int:
        cur = self.conn.execute(
            "INSERT INTO approvals(kind,object_hash,approver,approved_at,note) VALUES(?,?,?,?,?)",
            (kind, object_hash, approver, _now(), note),
        )
        self.conn.commit()
        return int(cur.lastrowid)

    def find_approval(self, *, kind: str, object_hash: str) -> sqlite3.Row | None:
        return self.conn.execute(
            "SELECT * FROM approvals WHERE kind=? AND object_hash=? ORDER BY approval_id DESC",
            (kind, object_hash),
        ).fetchone()

    def start_session(self, session_id: str, *, run_id: str, persona: str, seed: int,
                      attempts: int = 1) -> None:
        self.conn.execute(
            "INSERT OR REPLACE INTO sessions(session_id,run_id,persona,seed,started_at,"
            "ended_at,status,attempts) VALUES(?,?,?,?,?,NULL,'running',?)",
            (session_id, run_id, persona, seed, _now(), attempts),
        )
        self.conn.commit()

    def end_session(self, session_id: str, *, status: str) -> None:
        self.conn.execute(
            "UPDATE sessions SET ended_at=?, status=? WHERE session_id=?",
            (_now(), status, session_id),
        )
        self.conn.commit()

    # --- events and screenshots ----------------------------------------------

    def append_event(self, session_id: str, *, type: str, payload: dict[str, Any],
                     level_no: int | None = None, screenshot_id: str | None = None) -> str:
        prev = self._chain.get(session_id) or integrity.chain_start(session_id)
        seq = self._seq.get(session_id, 0) + 1
        event = {
            "session_id": session_id,
            "seq": seq,
            "ts": _now(),
            "type": type,
            "level_no": level_no,
            # Redact on the way in (S12), so nothing downstream -- the hash
            # chain, the dashboard, an exported pack -- can leak a credential.
            "payload": integrity.canonical_json(integrity.redact(payload)),
            "screenshot_id": screenshot_id,
        }
        row_hash = integrity.event_hash(prev, event)
        self.conn.execute(
            "INSERT INTO events(session_id,seq,ts,type,level_no,payload,screenshot_id,"
            "prev_hash,row_hash) VALUES(?,?,?,?,?,?,?,?,?)",
            (event["session_id"], event["seq"], event["ts"], event["type"], event["level_no"],
             event["payload"], event["screenshot_id"], prev, row_hash),
        )
        self.conn.commit()
        self._chain[session_id] = row_hash
        self._seq[session_id] = seq
        return row_hash

    def add_screenshot(self, screenshot_id: str, *, path: str | Path, sha256: str) -> None:
        self.conn.execute(
            "INSERT OR REPLACE INTO screenshots(screenshot_id,path,sha256,taken_at) VALUES(?,?,?,?)",
            (screenshot_id, str(Path(path).as_posix()), sha256, _now()),
        )
        self.conn.commit()

    # --- metrics, results, spend ---------------------------------------------

    def set_metric(self, session_id: str, metric: str, value: float | None) -> None:
        if value is None:
            return  # a missing metric is absent, never stored as a sentinel
        self.conn.execute(
            "INSERT OR REPLACE INTO metrics(session_id,metric,value) VALUES(?,?,?)",
            (session_id, metric, float(value)),
        )
        self.conn.commit()

    def write_result(self, run_id: str, row: dict[str, Any]) -> None:
        # Re-running the analysis replaces a result rather than duplicating it.
        self.conn.execute(
            "DELETE FROM results WHERE run_id=? AND contrast=? AND metric=?",
            (run_id, row["contrast"], row["metric"]),
        )
        self.conn.execute(
            "INSERT INTO results(run_id,contrast,metric,n_a,n_b,diff,ci_low,ci_high,hedges_g,"
            "p_raw,p_holm,tost_p,margin,verdict,prereg_hash,computed_at)"
            " VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (run_id, row["contrast"], row["metric"], row["n_a"], row["n_b"], row["diff"],
             row["ci_low"], row["ci_high"], row.get("hedges_g"), row.get("p_raw"),
             row.get("p_holm"), row.get("tost_p"), row.get("margin"), row.get("verdict"),
             row.get("prereg_hash", ""), _now()),
        )
        self.conn.commit()

    def record_spend(self, entry: dict[str, Any]) -> None:
        self.conn.execute(
            "INSERT INTO spend(ts,run_id,session_id,model,input_tokens,output_tokens,"
            "cache_creation_input_tokens,cache_read_input_tokens,usd) VALUES(?,?,?,?,?,?,?,?,?)",
            (_now(), entry.get("run_id", ""), entry.get("session_id", ""), entry.get("model", ""),
             entry.get("input_tokens", 0), entry.get("output_tokens", 0),
             entry.get("cache_creation_input_tokens", 0), entry.get("cache_read_input_tokens", 0),
             float(entry.get("usd", 0.0))),
        )
        self.conn.commit()

    def total_spend_usd(self, run_id: str | None = None) -> float:
        if run_id is None:
            row = self.conn.execute("SELECT COALESCE(SUM(usd),0) FROM spend").fetchone()
        else:
            row = self.conn.execute(
                "SELECT COALESCE(SUM(usd),0) FROM spend WHERE run_id=?", (run_id,)
            ).fetchone()
        return float(row[0])

    # --- graph ----------------------------------------------------------------

    def upsert_node(self, node_id: str, *, view: str, kind: str, label: str,
                    attrs: dict[str, Any] | None = None) -> None:
        self.conn.execute(
            "INSERT OR REPLACE INTO nodes(node_id,view,kind,label,attrs) VALUES(?,?,?,?,?)",
            (node_id, view, kind, label, integrity.canonical_json(attrs or {})),
        )
        self.conn.commit()

    def upsert_edge(self, edge_id: str, *, view: str, src: str, dst: str, kind: str,
                    certainty: str, source_url: str = "", claim: str = "",
                    evidence_ref: str = "", reviewed_by: str = "") -> None:
        self.conn.execute(
            "INSERT OR REPLACE INTO edges(edge_id,view,src,dst,kind,certainty,source_url,"
            "claim,evidence_ref,reviewed_by) VALUES(?,?,?,?,?,?,?,?,?,?)",
            (edge_id, view, src, dst, kind, certainty, source_url, claim, evidence_ref, reviewed_by),
        )
        self.conn.commit()

    # --- integrity ------------------------------------------------------------

    def write_manifest(self, run_id: str, directory: str | Path = "data/manifests") -> Path:
        return integrity.write_manifest(self.conn, run_id, directory)

    def verify(self, run_id: str, directory: str | Path = "data/manifests") -> dict[str, Any]:
        return integrity.verify_manifest(self.conn, run_id, directory)
