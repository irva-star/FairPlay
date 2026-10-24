"""`python -m evidence verify <run_id>` — recompute every hash and report.

Prints OK, or the first mismatch and which file it is in.
"""

from __future__ import annotations

import argparse
import sys

from evidence import integrity
from evidence.store import DEFAULT_DB, EvidenceStore


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="python -m evidence")
    sub = ap.add_subparsers(dest="command", required=True)

    v = sub.add_parser("verify", help="verify a run's hash chain and screenshots")
    v.add_argument("run_id")
    v.add_argument("--db", default=str(DEFAULT_DB))
    v.add_argument("--manifests", default="data/manifests")

    m = sub.add_parser("manifest", help="write a run's manifest")
    m.add_argument("run_id")
    m.add_argument("--db", default=str(DEFAULT_DB))
    m.add_argument("--manifests", default="data/manifests")

    args = ap.parse_args(argv)

    with EvidenceStore(args.db, read_only=(args.command == "verify")) as store:
        if args.command == "manifest":
            path = store.write_manifest(args.run_id, args.manifests)
            print(f"wrote {path}")
            return 0

        try:
            manifest = integrity.verify_manifest(store.conn, args.run_id, args.manifests)
        except integrity.IntegrityError as exc:
            print(f"FAILED: {exc}")
            return 1
        sessions = len(manifest["session_final_hashes"])
        shots = len(manifest["screenshot_hashes"])
        print(f"OK: run {args.run_id}, {sessions} sessions, {shots} screenshots, all hashes match")
        return 0


if __name__ == "__main__":
    sys.exit(main())
