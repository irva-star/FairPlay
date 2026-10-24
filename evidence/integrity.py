"""Hash chain and manifests (SPEC section 7).

This is tamper-EVIDENT, not tamper-proof: anyone with write access could
rebuild the chain from scratch. Committing each run's manifest hash to git
before analysis closes most of that gap, because the git history is somewhere
else.

    row_hash = sha256(prev_hash + canonical_json(event))

and the chain starts at sha256(session_id).
"""

from __future__ import annotations

import hashlib
import json
import re
from pathlib import Path
from typing import Any

CHAIN_FIELDS = ("session_id", "seq", "ts", "type", "level_no", "payload", "screenshot_id")


def canonical_json(obj: Any) -> str:
    """Sorted keys, no spaces. Two equal payloads must hash identically."""
    return json.dumps(obj, sort_keys=True, separators=(",", ":"), ensure_ascii=False)


# Rule S12: a key must never reach the evidence store, a log or the dashboard.
# Redaction happens on the way in, so nothing downstream has to remember to do it.
SECRET_KEY_NAMES = frozenset({
    "api_key", "apikey", "anthropic_api_key", "authorization", "auth", "token",
    "access_token", "secret", "password", "cookie", "set-cookie",
})
_SECRET_VALUE_RE = re.compile(r"sk-[A-Za-z0-9_\-]{8,}")
REDACTED = "[redacted]"


def redact(obj: Any) -> Any:
    """Strip anything that looks like a credential, by key name or by shape."""
    if isinstance(obj, dict):
        return {
            k: (REDACTED if str(k).lower() in SECRET_KEY_NAMES else redact(v))
            for k, v in obj.items()
        }
    if isinstance(obj, (list, tuple)):
        return [redact(v) for v in obj]
    if isinstance(obj, str):
        return _SECRET_VALUE_RE.sub(REDACTED, obj)
    return obj


def sha256_hex(data: str | bytes) -> str:
    if isinstance(data, str):
        data = data.encode("utf-8")
    return hashlib.sha256(data).hexdigest()


def chain_start(session_id: str) -> str:
    return sha256_hex(session_id)


def event_hash(prev_hash: str, event: dict[str, Any]) -> str:
    row = {k: event.get(k) for k in CHAIN_FIELDS}
    return sha256_hex(prev_hash + canonical_json(row))


def file_sha256(path: str | Path, *, chunk: int = 65536) -> str:
    h = hashlib.sha256()
    with Path(path).open("rb") as fh:
        while block := fh.read(chunk):
            h.update(block)
    return h.hexdigest()


class IntegrityError(Exception):
    """Raised with the first mismatch found, so the message names the file."""


def verify_sessions(conn, run_id: str) -> dict[str, str]:
    """Recompute every chain in the run. Returns session_id -> final hash."""
    finals: dict[str, str] = {}
    sessions = [r[0] for r in conn.execute(
        "SELECT session_id FROM sessions WHERE run_id=? ORDER BY session_id", (run_id,)
    )]
    for session_id in sessions:
        prev = chain_start(session_id)
        rows = conn.execute(
            "SELECT event_id, session_id, seq, ts, type, level_no, payload, screenshot_id,"
            " prev_hash, row_hash FROM events WHERE session_id=? ORDER BY seq",
            (session_id,),
        ).fetchall()
        for row in rows:
            event = {
                "session_id": row[1], "seq": row[2], "ts": row[3], "type": row[4],
                "level_no": row[5], "payload": row[6], "screenshot_id": row[7],
            }
            if row[8] != prev:
                raise IntegrityError(
                    f"session {session_id} event seq {row[2]}: prev_hash does not match the chain"
                )
            expected = event_hash(prev, event)
            if row[9] != expected:
                raise IntegrityError(
                    f"session {session_id} event seq {row[2]}: row_hash mismatch "
                    f"(event {row[0]} was altered)"
                )
            prev = expected
        finals[session_id] = prev
    return finals


def verify_screenshots(conn, run_id: str) -> dict[str, str]:
    """Rehash every PNG this run referenced."""
    rows = conn.execute(
        "SELECT DISTINCT s.screenshot_id, s.path, s.sha256 FROM screenshots s "
        "JOIN events e ON e.screenshot_id = s.screenshot_id "
        "JOIN sessions ss ON ss.session_id = e.session_id "
        "WHERE ss.run_id=? ORDER BY s.screenshot_id",
        (run_id,),
    ).fetchall()
    hashes: dict[str, str] = {}
    for screenshot_id, path, recorded in rows:
        p = Path(path)
        if not p.exists():
            raise IntegrityError(f"screenshot {screenshot_id}: file is missing at {path}")
        actual = file_sha256(p)
        if actual != recorded:
            raise IntegrityError(f"screenshot {screenshot_id}: sha256 mismatch for {path}")
        hashes[screenshot_id] = actual
    return hashes


def build_manifest(conn, run_id: str) -> dict[str, Any]:
    run = conn.execute(
        "SELECT run_id, label, created_at, config_hash, plan_hash, approval_id, git_commit, status"
        " FROM runs WHERE run_id=?", (run_id,)
    ).fetchone()
    if run is None:
        raise IntegrityError(f"no such run: {run_id}")

    manifest = {
        "run_id": run[0],
        "label": run[1],
        "created_at": run[2],
        "config_hash": run[3],
        "plan_hash": run[4],
        "approval_id": run[5],
        "git_commit": run[6],
        "status": run[7],
        "session_final_hashes": verify_sessions(conn, run_id),
        "screenshot_hashes": verify_screenshots(conn, run_id),
    }
    manifest["manifest_sha256"] = sha256_hex(canonical_json(manifest))
    return manifest


def write_manifest(conn, run_id: str, directory: str | Path = "data/manifests") -> Path:
    manifest = build_manifest(conn, run_id)
    out = Path(directory)
    out.mkdir(parents=True, exist_ok=True)
    path = out / f"{run_id}.json"
    path.write_text(json.dumps(manifest, indent=2, sort_keys=True), encoding="utf-8")
    return path


def verify_manifest(conn, run_id: str, directory: str | Path = "data/manifests") -> dict[str, Any]:
    """Recompute everything and compare against the manifest written at run end."""
    path = Path(directory) / f"{run_id}.json"
    if not path.exists():
        raise IntegrityError(f"no manifest for run {run_id} at {path}")
    recorded = json.loads(path.read_text(encoding="utf-8"))

    claimed = recorded.pop("manifest_sha256", None)
    if sha256_hex(canonical_json(recorded)) != claimed:
        raise IntegrityError(f"manifest {path} was altered: its own hash does not match")
    recorded["manifest_sha256"] = claimed

    current = build_manifest(conn, run_id)
    for session_id, final in recorded["session_final_hashes"].items():
        if current["session_final_hashes"].get(session_id) != final:
            raise IntegrityError(f"session {session_id}: chain no longer matches the manifest")
    for shot_id, digest in recorded["screenshot_hashes"].items():
        if current["screenshot_hashes"].get(shot_id) != digest:
            raise IntegrityError(f"screenshot {shot_id}: no longer matches the manifest")
    return current
