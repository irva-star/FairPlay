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
if str(REPO) not in sys.path:
    sys.path.insert(0, str(REPO))
BATCHES = {
    "pilot": "configs/run_pilot.yaml",
    "on": "configs/run_on.yaml",
    "off": "configs/run_off.yaml",
    "sv-live": "configs/run_sv_live.yaml",
    "sv": "configs/run_sv.yaml",
    "on-quick": "configs/run_on_quick.yaml",
    "off-quick": "configs/run_off_quick.yaml",
}
COPY_NAMES = ("a", "b", "c", "d")
RULES_FILE = REPO / "oracle" / "judge_rules.secret"


def sealed_copy_rules() -> str:
    """JUDGE_RULES for the four hosted copies: each rule exactly once, shuffled.

    Taken from the JUDGE_RULES environment variable if set (so the hosted
    service and the audit use the same string), otherwise generated once and
    kept in oracle/ (gitignored) without being printed. Paste that file's
    contents into the host's secret environment without reading it.
    """
    import secrets

    if os.environ.get("JUDGE_RULES"):
        return os.environ["JUDGE_RULES"]
    if RULES_FILE.exists():
        return RULES_FILE.read_text(encoding="utf-8").strip()
    rules = list(JUDGE_RULES)
    secrets.SystemRandom().shuffle(rules)
    value = ",".join(f"{c}={r}" for c, r in zip(COPY_NAMES, rules))
    RULES_FILE.parent.mkdir(parents=True, exist_ok=True)
    RULES_FILE.write_text(value + "\n", encoding="utf-8")
    return value


def run_copies(*, llm: str, concurrency: int | None) -> int:
    """Serve all four copies under sealed rules and audit each one in turn."""
    proc = start_server({"JUDGE_RULES": sealed_copy_rules()}, SERVERS["streamvault"])
    try:
        for c in COPY_NAMES:
            cmd = [sys.executable, "-m", "walker", "run", f"configs/run_sv_copy_{c}.yaml", "--llm", llm]
            if concurrency:
                cmd += ["--concurrency", str(concurrency)]
            print(f"auditing copy {c.upper()}")
            code = subprocess.call(cmd, cwd=REPO)
            if code != 0:
                return code
        return 0
    finally:
        stop_server(proc)
SERVERS = {"doorrun": "game.server:app", "streamvault": "streamvault.server:app"}
JUDGE_RULES = ("none", "fee", "maze", "preticked")


def batch_settings(config_path: str) -> tuple[str, str]:
    """(app, switch) read from the batch config itself: one source of truth."""
    import yaml

    from walker.cli import on_off

    cfg = yaml.safe_load((REPO / config_path).read_text(encoding="utf-8"))
    app = str(cfg.get("app", "doorrun"))
    switch = on_off(cfg.get("switch", "off")) if app == "doorrun" else ""
    return app, switch
HOST, PORT = "127.0.0.1", 8000


def port_open(host: str = HOST, port: int = PORT, timeout: float = 0.25) -> bool:
    try:
        with socket.create_connection((host, port), timeout=timeout):
            return True
    except OSError:
        return False


def start_server(env_extra: dict[str, str], module: str = "game.server:app") -> subprocess.Popen:
    if port_open():
        raise RuntimeError(
            f"something is already listening on {HOST}:{PORT}. Stop it first, otherwise "
            "the batch could run against a server with the wrong switch setting."
        )
    env = {**os.environ, **env_extra}
    shown = {k: ("(sealed)" if k.startswith("JUDGE_RULE") else v) for k, v in env_extra.items()}
    print(f"starting {module} with {shown}")
    proc = subprocess.Popen(
        [sys.executable, "-m", "uvicorn", module,
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
    print("stopping server")
    proc.terminate()
    try:
        proc.wait(timeout=10)
    except subprocess.TimeoutExpired:
        proc.kill()
        proc.wait(timeout=5)


def run_batch(name: str, *, llm: str, concurrency: int | None, judge_rule: str) -> int:
    config = BATCHES[name]
    app, switch = batch_settings(config)
    if app == "doorrun":
        proc = start_server({"PLANTED_SWITCH": switch}, SERVERS[app])
    else:
        # "secret" picks a rule nobody at the laptop sees. It is written only to
        # the oracle, which validation/ reveals after the audit has named it.
        import secrets

        rule = secrets.choice(JUDGE_RULES) if judge_rule == "secret" else judge_rule
        proc = start_server({"JUDGE_RULE": rule}, SERVERS[app])
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
    ap.add_argument("--batch", action="append", choices=sorted([*BATCHES, "copies"]), required=True,
                    help="may be repeated; batches run in the order given")
    ap.add_argument("--llm", choices=["real", "fake"], default="real")
    ap.add_argument("--concurrency", type=int, default=None)
    ap.add_argument("--judge-rule", default="secret", choices=[*JUDGE_RULES, "secret"],
                    help="StreamVault only: the hidden rule (default: secret, chosen at random)")
    args = ap.parse_args(argv)

    for name in args.batch:
        if name == "copies":
            code = run_copies(llm=args.llm, concurrency=args.concurrency)
        else:
            code = run_batch(name, llm=args.llm, concurrency=args.concurrency,
                             judge_rule=args.judge_rule)
        if code != 0:
            print(f"batch {name} exited {code}; stopping here", file=sys.stderr)
            return code
    return 0


if __name__ == "__main__":
    sys.exit(main())
