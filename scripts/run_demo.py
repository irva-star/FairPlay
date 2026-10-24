"""Orchestrator (SPEC section 4).

Restarts the game server with the switch on or off before each batch, runs the
approved batch, then stops it. The switch is only settable at server start, so
"restart the server" is the only way to change condition -- which is the point.

    python scripts/run_demo.py --batch pilot
    python scripts/run_demo.py --batch on --batch off

Note: `walker run` refuses to start without a recorded approval for that exact
plan, so this script does not and cannot bypass the human gate. Approve first:

    python -m walker plan configs/run_on.yaml
    python -m walker approve <plan_hash> --name "Your Name"
"""

from __future__ import annotations

import argparse
import os
import socket
import subprocess
import sys
import time
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
BATCHES = {
    "pilot": ("configs/run_pilot.yaml", "on"),
    "on": ("configs/run_on.yaml", "on"),
    "off": ("configs/run_off.yaml", "off"),
}
HOST, PORT = "127.0.0.1", 8000


def port_open(host: str = HOST, port: int = PORT, timeout: float = 0.25) -> bool:
    try:
        with socket.create_connection((host, port), timeout=timeout):
            return True
    except OSError:
        return False


def start_server(switch: str) -> subprocess.Popen:
    if port_open():
        raise RuntimeError(
            f"something is already listening on {HOST}:{PORT}. Stop it first, otherwise "
            "the batch could run against a server with the wrong switch setting."
        )
    env = {**os.environ, "PLANTED_SWITCH": switch}
    print(f"starting game server with PLANTED_SWITCH={switch}")
    proc = subprocess.Popen(
        [sys.executable, "-m", "uvicorn", "game.server:app",
         "--host", HOST, "--port", str(PORT), "--workers", "1", "--log-level", "warning"],
        cwd=REPO, env=env,
    )
    deadline = time.monotonic() + 30
    while time.monotonic() < deadline:
        if proc.poll() is not None:
            raise RuntimeError(f"server exited early with code {proc.returncode}")
        if port_open():
            print(f"  up on http://{HOST}:{PORT}")
            return proc
        time.sleep(0.2)
    proc.terminate()
    raise RuntimeError("server did not come up within 30s")


def stop_server(proc: subprocess.Popen) -> None:
    print("stopping game server")
    proc.terminate()
    try:
        proc.wait(timeout=10)
    except subprocess.TimeoutExpired:
        proc.kill()
        proc.wait(timeout=5)


def run_batch(name: str, *, llm: str, concurrency: int | None) -> int:
    config, switch = BATCHES[name]
    proc = start_server(switch)
    try:
        cmd = [sys.executable, "-m", "walker", "run", config, "--llm", llm]
        if concurrency:
            cmd += ["--concurrency", str(concurrency)]
        print(f"running batch {name}: {' '.join(cmd)}")
        return subprocess.call(cmd, cwd=REPO)
    finally:
        stop_server(proc)


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="Run batches against a correctly-switched server.")
    ap.add_argument("--batch", action="append", choices=sorted(BATCHES), required=True,
                    help="may be repeated; batches run in the order given")
    ap.add_argument("--llm", choices=["real", "fake"], default="real")
    ap.add_argument("--concurrency", type=int, default=None)
    args = ap.parse_args(argv)

    for name in args.batch:
        code = run_batch(name, llm=args.llm, concurrency=args.concurrency)
        if code != 0:
            print(f"batch {name} exited {code}; stopping here", file=sys.stderr)
            return code
    return 0


if __name__ == "__main__":
    sys.exit(main())
