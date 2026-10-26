"""Analyse and reveal the four spot-the-trap copies in one go.

    python scripts/analyse_copies.py

For each label copy-a .. copy-d, takes the most recent run, computes the
statistics, then opens the sealed rule and writes the reveal file the
dashboard shows. Run scripts/export_snapshot.py afterwards to publish.
"""

from __future__ import annotations

import sqlite3
import subprocess
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
DB = REPO / "data" / "evidence.db"


def latest_run(conn: sqlite3.Connection, label: str) -> str | None:
    row = conn.execute(
        "SELECT run_id FROM runs WHERE label=? AND status='complete' ORDER BY created_at DESC LIMIT 1",
        (label,),
    ).fetchone()
    return row[0] if row else None


def main() -> int:
    if not DB.exists():
        print(f"no evidence database at {DB}", file=sys.stderr)
        return 1
    conn = sqlite3.connect(f"file:{DB.as_posix()}?mode=ro", uri=True)
    runs = {c: latest_run(conn, f"copy-{c}") for c in "abcd"}
    conn.close()

    score = 0
    for copy, run_id in runs.items():
        print(f"\n=== copy {copy.upper()}: {run_id or 'no completed run'}")
        if not run_id:
            continue
        subprocess.call([sys.executable, "-m", "stats", run_id], cwd=REPO,
                        stdout=subprocess.DEVNULL)
        code = subprocess.call([sys.executable, "-m", "validation.sv_reveal", run_id, "--write"],
                               cwd=REPO)
        score += code == 0
    print(f"\nengine named {score} of {sum(1 for r in runs.values() if r)} hidden rules correctly")
    return 0


if __name__ == "__main__":
    sys.exit(main())
