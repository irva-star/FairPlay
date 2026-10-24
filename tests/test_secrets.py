"""Rule S12: where the API key lives, and everywhere it must not reach."""

from __future__ import annotations

import ast
import io
import os
import re
from contextlib import redirect_stderr
from pathlib import Path

import pytest

from walker.cli import load_env_file

REPO = Path(__file__).resolve().parent.parent
KEY = "ANTHROPIC_API_KEY"


@pytest.fixture()
def env_file(tmp_path: Path, monkeypatch):
    path = tmp_path / ".env"
    path.write_text(
        "# a comment\n"
        "\n"
        'ANTHROPIC_API_KEY="sk-ant-fake-value-for-test"\n'
        "OTHER=plain\n"
        "QUOTED='single'\n"
        "MALFORMED\n",
        encoding="utf-8",
    )
    for name in (KEY, "OTHER", "QUOTED", "MALFORMED"):
        monkeypatch.delenv(name, raising=False)
    return path


def test_the_loader_reads_the_key_into_the_environment(env_file) -> None:
    load_env_file(env_file)
    assert os.environ[KEY] == "sk-ant-fake-value-for-test"


def test_the_loader_returns_names_never_values(env_file) -> None:
    """Whatever this returns may end up in a log line, so it must be names only."""
    names = load_env_file(env_file)
    assert set(names) == {KEY, "OTHER", "QUOTED"}
    assert not any("sk-ant" in name for name in names)


def test_quotes_are_stripped_and_junk_is_skipped(env_file) -> None:
    load_env_file(env_file)
    assert os.environ["OTHER"] == "plain"
    assert os.environ["QUOTED"] == "single"
    assert "MALFORMED" not in os.environ


def test_an_exported_key_is_not_overridden(env_file, monkeypatch) -> None:
    """A key you exported on purpose must win over a stale .env."""
    monkeypatch.setenv(KEY, "exported-wins")
    load_env_file(env_file)
    assert os.environ[KEY] == "exported-wins"


def test_a_missing_env_file_is_not_an_error(tmp_path: Path) -> None:
    assert load_env_file(tmp_path / "nope") == []
    assert load_env_file("") == []


def test_run_refuses_without_a_key_and_does_not_echo_one(tmp_path: Path, monkeypatch) -> None:
    pytest.importorskip("yaml")
    from evidence.store import EvidenceStore
    from walker.cli import build_plan, cmd_run

    monkeypatch.delenv(KEY, raising=False)
    db = tmp_path / "evidence.db"
    plan = build_plan("configs/run_pilot.yaml")
    with EvidenceStore(db) as store:
        store.record_approval(kind="run_plan", object_hash=plan.plan_hash, approver="Tester")

    class Args:
        config = "configs/run_pilot.yaml"
        pricing = "configs/pricing.yaml"
        concurrency = 1
        llm = "real"

        def __init__(self, db: str) -> None:
            self.db = db

    stderr = io.StringIO()
    with redirect_stderr(stderr):
        code = cmd_run(Args(str(db)))

    message = stderr.getvalue()
    assert code == 2, "an approved plan must still refuse to run without a key"
    assert KEY in message
    assert "--llm fake" in message, "the message should offer the zero-cost path"
    assert "sk-" not in message


def test_the_fake_llm_path_needs_no_key(monkeypatch) -> None:
    """A smoke run must work on a machine that has never seen a key."""
    monkeypatch.delenv(KEY, raising=False)
    from walker.llm import FakeLLM

    assert FakeLLM(seed=1) is not None


# --- static guarantees --------------------------------------------------------

def python_files(*folders: str) -> list[Path]:
    return [p for folder in folders for p in sorted((REPO / folder).rglob("*.py"))]


def test_only_the_cli_and_llm_module_mention_the_key() -> None:
    """The fewer places that name the key, the fewer places can leak it."""
    mentioning = {
        path.relative_to(REPO)
        for path in python_files("game", "walker", "evidence", "stats",
                                 "validation", "contextgraph", "dashboard")
        if KEY in path.read_text(encoding="utf-8")
    }
    assert mentioning == {Path("walker/cli.py")}, mentioning


def imported_modules(path: Path) -> set[str]:
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    found: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            found.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and not node.level and node.module:
            found.add(node.module)
    return found


def test_the_game_server_and_dashboard_never_read_the_key() -> None:
    """Checks imports rather than text: a docstring may name what it refuses to use."""
    for path in python_files("game", "dashboard"):
        assert KEY not in path.read_text(encoding="utf-8"), (
            f"{path.relative_to(REPO)} names the API key"
        )
        offenders = sorted(m for m in imported_modules(path)
                           if m.split(".")[0] == "anthropic")
        assert not offenders, f"{path.relative_to(REPO)} imports {offenders}"


# `.env` as a filename, not as the tail of `os.environ`.
_ENV_FILE_RE = re.compile(r"\.env\b")


def test_nothing_loads_the_env_file_except_the_walker() -> None:
    """The hosted containers must not even have a code path that reads .env."""
    for path in python_files("game", "dashboard", "evidence", "stats",
                             "validation", "contextgraph"):
        source = path.read_text(encoding="utf-8")
        assert "load_env_file" not in source, path.relative_to(REPO)
        assert not _ENV_FILE_RE.search(source), (
            f"{path.relative_to(REPO)} references the .env file"
        )


def test_the_key_is_never_passed_into_an_event_payload() -> None:
    """Redaction is the backstop; not putting it there is the actual rule."""
    for path in python_files("walker"):
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if isinstance(node, ast.Constant) and isinstance(node.value, str):
                assert "sk-ant" not in node.value, f"{path.relative_to(REPO)}:{node.lineno}"


def test_gitignore_covers_the_key_and_the_original_spec() -> None:
    patterns = (REPO / ".gitignore").read_text(encoding="utf-8").splitlines()
    for required in (".env", ".env.*", "data/", "oracle/", "*.docx"):
        assert required in patterns, f"{required} must be gitignored"


def test_no_committed_file_contains_a_real_looking_key() -> None:
    """Catches a key pasted into a config or notebook before it is pushed."""
    pattern = re.compile(r"sk-ant-[A-Za-z0-9_\-]{20,}")
    skip_dirs = {".git", ".venv", "__pycache__", "data", "oracle", "node_modules"}
    offenders: list[str] = []

    for path in REPO.rglob("*"):
        if not path.is_file() or any(part in skip_dirs for part in path.parts):
            continue
        if path.suffix.lower() not in {".py", ".yaml", ".yml", ".json", ".toml", ".md",
                                       ".txt", ".ini", ".js", ".html", ".css", ".sql"}:
            continue
        try:
            text = path.read_text(encoding="utf-8")
        except (UnicodeDecodeError, OSError):
            continue
        for match in pattern.findall(text):
            # The redaction tests deliberately contain obviously-fake keys.
            if "FAKE" in match.upper() or "TEST" in match.upper() or "KEY" in match.upper():
                continue
            offenders.append(f"{path.relative_to(REPO)}: {match[:14]}…")

    assert not offenders, f"possible real keys committed: {offenders}"
