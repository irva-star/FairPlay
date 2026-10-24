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
    count = copy_referenced_screenshots(src, shots_out)
    rewrite_screenshot_paths(db_out, "snapshot/screenshots")

    print(f"wrote {db_out}")
    print(f"copied {count} screenshot(s) to {shots_out}")
    print("commit snapshot/ so public mode has data to read")
    return 0


if __name__ == "__main__":
    sys.exit(main())
