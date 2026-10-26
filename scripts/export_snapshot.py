"""Build the committed public snapshot (SPEC section 7).

Copies the evidence database with SQLite's backup API -- not a file copy, which
is unsafe while WAL is active -- plus only the screenshots it actually
references, into snapshot/. That folder is committed because it holds nothing
but our own test-game data.

Public dashboard mode reads snapshot/ and nothing else, so anything omitted here
simply does not exist as far as the deployed site is concerned.

    python scripts/export_snapshot.py
"""

from __future__ import annotations

import argparse
import shutil
import sqlite3
import sys
from pathlib import Path

DEFAULT_SRC = Path("data/evidence.db")
DEFAULT_OUT = Path("snapshot")

# Nothing here holds an API key, but be explicit about what the public copy drops.
DROP_TABLES = ()


def initials(name: str) -> str:
    """'Sarah Ng' -> 'S.N.' The public snapshot never carries a full name (H1)."""
    parts = [p for p in name.replace(".", " ").split() if p]
    return "".join(f"{p[0].upper()}." for p in parts) or "?"


def scrub_people(db: Path) -> int:
    conn = sqlite3.connect(db)
    try:
        rows = conn.execute("SELECT approval_id, approver FROM approvals").fetchall()
        for approval_id, approver in rows:
            conn.execute("UPDATE approvals SET approver=?, note='' WHERE approval_id=?",
                         (initials(approver or ""), approval_id))
        conn.commit()
        conn.execute("VACUUM")
        return len(rows)
    finally:
        conn.close()


def drop_unfinished_runs(db: Path) -> list[str]:
    """Interrupted runs stay in the local store (they're attrition) but not in public."""
    conn = sqlite3.connect(db)
    try:
        runs = [r[0] for r in conn.execute("SELECT run_id FROM runs WHERE status != 'complete'")]
        for run_id in runs:
            sessions = [r[0] for r in conn.execute("SELECT session_id FROM sessions WHERE run_id=?", (run_id,))]
            for sid in sessions:
                conn.execute("DELETE FROM events WHERE session_id=?", (sid,))
                conn.execute("DELETE FROM metrics WHERE session_id=?", (sid,))
            conn.execute("DELETE FROM sessions WHERE run_id=?", (run_id,))
            conn.execute("DELETE FROM results WHERE run_id=?", (run_id,))
            conn.execute("DELETE FROM spend WHERE run_id=?", (run_id,))
            conn.execute("DELETE FROM runs WHERE run_id=?", (run_id,))
        conn.execute("DELETE FROM screenshots WHERE screenshot_id NOT IN "
                     "(SELECT screenshot_id FROM events WHERE screenshot_id IS NOT NULL)")
        conn.commit()
        conn.execute("VACUUM")
        return runs
    finally:
        conn.close()


def copy_validation(src_dir: Path, out_dir: Path) -> int:
    if not src_dir.exists():
        return 0
    out_dir.mkdir(parents=True, exist_ok=True)
    n = 0
    for path in src_dir.glob("*.json"):
        shutil.copy2(path, out_dir / path.name)
        n += 1
    return n


def backup_db(src: Path, dst: Path) -> None:
    dst.parent.mkdir(parents=True, exist_ok=True)
    if dst.exists():
        dst.unlink()
    source = sqlite3.connect(f"file:{src.as_posix()}?mode=ro", uri=True)
    target = sqlite3.connect(dst)
    try:
        source.backup(target)
        for table in DROP_TABLES:
            target.execute(f"DELETE FROM {table}")
        target.commit()
        target.execute("VACUUM")
    finally:
        source.close()
        target.close()


def copy_referenced_screenshots(db: Path, out_dir: Path) -> int:
    conn = sqlite3.connect(f"file:{db.as_posix()}?mode=ro", uri=True)
    try:
        rows = conn.execute(
            "SELECT DISTINCT path FROM screenshots WHERE screenshot_id IN "
            "(SELECT screenshot_id FROM events WHERE screenshot_id IS NOT NULL)"
        ).fetchall()
    finally:
        conn.close()

    out_dir.mkdir(parents=True, exist_ok=True)
    copied = 0
    for (path,) in rows:
        src = Path(path)
        if not src.exists():
            print(f"  warning: referenced screenshot is missing: {src}", file=sys.stderr)
            continue
        shutil.copy2(src, out_dir / src.name)
        copied += 1
    return copied


def rewrite_screenshot_paths(db: Path, prefix: str) -> None:
    """Point the snapshot's rows at the snapshot's own screenshot folder."""
    conn = sqlite3.connect(db)
    try:
        for screenshot_id, path in conn.execute("SELECT screenshot_id, path FROM screenshots"):
            conn.execute("UPDATE screenshots SET path=? WHERE screenshot_id=?",
                         (f"{prefix}/{Path(path).name}", screenshot_id))
        conn.commit()
    finally:
        conn.close()


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="Export the public snapshot.")
    ap.add_argument("--src", default=str(DEFAULT_SRC))
    ap.add_argument("--out", default=str(DEFAULT_OUT))
    args = ap.parse_args(argv)

    src, out = Path(args.src), Path(args.out)
    if not src.exists():
        print(f"no evidence database at {src}", file=sys.stderr)
        return 1

    db_out = out / "evidence.db"
    shots_out = out / "screenshots"
    backup_db(src, db_out)
    dropped = drop_unfinished_runs(db_out)
    print(f"left out {len(dropped)} unfinished run(s)")
    count = copy_referenced_screenshots(db_out, shots_out)
    rewrite_screenshot_paths(db_out, "snapshot/screenshots")
    scrubbed = scrub_people(db_out)
    reveals = copy_validation(Path("data/validation"), out / "validation")
    print(f"replaced {scrubbed} approver name(s) with initials")
    print(f"copied {reveals} validation file(s)")

    print(f"wrote {db_out}")
    print(f"copied {count} screenshot(s) to {shots_out}")
    print("commit snapshot/ so public mode has data to read")
    return 0


if __name__ == "__main__":
    sys.exit(main())
