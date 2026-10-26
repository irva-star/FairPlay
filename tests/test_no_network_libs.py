"""Rule S6: network code is contained.

`anthropic` may be imported in exactly one file. No app folder may import a
general-purpose network library at all. Parsed with `ast` rather than grepped,
so `urllib.parse` (which the guard legitimately uses to split URLs) is not
confused with `urllib.request`.

tests/ is exempt because FastAPI's test client needs httpx, and scripts/ is
exempt because the orchestrator checks whether a local port is listening.
"""

from __future__ import annotations

import ast
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parent.parent

APP_FOLDERS = ("game", "streamvault", "walker", "evidence", "stats", "validation", "dashboard", "hub")

BANNED_MODULES = {
    "requests", "httpx", "aiohttp", "urllib.request", "urllib3", "socket",
    "http.client", "ftplib", "telnetlib", "smtplib", "websockets", "pycurl",
}

ANTHROPIC_ALLOWED_IN = {Path("walker/llm.py")}


def python_files() -> list[Path]:
    files: list[Path] = []
    for folder in APP_FOLDERS:
        files.extend(sorted((REPO / folder).rglob("*.py")))
    return files


def imported_modules(path: Path) -> set[str]:
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    found: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                found.add(alias.name)
        elif isinstance(node, ast.ImportFrom):
            if node.level:  # relative import, never a third-party network lib
                continue
            if node.module:
                found.add(node.module)
                for alias in node.names:
                    found.add(f"{node.module}.{alias.name}")
    return found


def is_banned(module: str) -> bool:
    return any(module == banned or module.startswith(banned + ".")
               for banned in BANNED_MODULES)


@pytest.mark.parametrize("path", python_files(), ids=lambda p: str(p.relative_to(REPO)))
def test_no_network_library_imports(path: Path) -> None:
    offenders = sorted(m for m in imported_modules(path) if is_banned(m))
    assert not offenders, (
        f"{path.relative_to(REPO)} imports {offenders}. App code must not reach the "
        "network; if a request is genuinely needed, it belongs behind walker/llm.py."
    )


@pytest.mark.parametrize("path", python_files(), ids=lambda p: str(p.relative_to(REPO)))
def test_anthropic_imported_in_one_file_only(path: Path) -> None:
    relative = path.relative_to(REPO)
    imports_anthropic = any(
        m == "anthropic" or m.startswith("anthropic.") for m in imported_modules(path)
    )
    if relative in ANTHROPIC_ALLOWED_IN:
        return
    assert not imports_anthropic, (
        f"{relative} imports anthropic. Rule S6 keeps every model call in walker/llm.py "
        "so there is exactly one place that can talk to an API."
    )


def test_llm_module_is_the_only_anthropic_importer() -> None:
    """The allowlisted file must actually be the one that imports it."""
    importers = {
        path.relative_to(REPO)
        for path in python_files()
        if any(m == "anthropic" or m.startswith("anthropic.") for m in imported_modules(path))
    }
    assert importers == ANTHROPIC_ALLOWED_IN, (
        f"expected anthropic only in {ANTHROPIC_ALLOWED_IN}, found {importers}"
    )


def test_stats_never_reads_the_oracle() -> None:
    """Only validation/ may read ground truth.

    Checks for the ground-truth filename and for an import of the validation
    module, rather than for the word "oracle" -- a docstring is allowed to
    explain the rule it is obeying.
    """
    for path in sorted((REPO / "stats").rglob("*.py")):
        text = path.read_text(encoding="utf-8")
        assert "ground_truth" not in text, (
            f"{path.relative_to(REPO)} references the ground-truth log. An estimator "
            "that can read the answer key proves nothing, which is the point of V5."
        )
        modules = imported_modules(path)
        offenders = sorted(m for m in modules if m.split(".")[0] == "validation")
        assert not offenders, (
            f"{path.relative_to(REPO)} imports {offenders}, which can read oracle/."
        )


def test_only_validation_references_the_ground_truth_log() -> None:
    referencing = {
        path.relative_to(REPO)
        for folder in APP_FOLDERS
        for path in (REPO / folder).rglob("*.py")
        if "ground_truth" in path.read_text(encoding="utf-8")
    }
    # game/server.py writes it; validation/ reads it. Nothing else touches it.
    assert referencing == {Path("game/server.py"),
                           Path("validation/score_against_oracle.py")}, referencing


def test_public_dashboard_never_imports_walker_or_anthropic() -> None:
    """Public mode must not even be able to construct a model client."""
    for name in ("common.py", "app.py"):
        modules = imported_modules(REPO / "dashboard" / name)
        offenders = sorted(m for m in modules if m.split(".")[0] in {"walker", "anthropic"})
        assert not offenders, (
            f"dashboard/{name} imports {offenders} at module level. The deployed "
            "dashboard has no API key and no reason to be able to start a run."
        )
