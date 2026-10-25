"""T9: the hosted images carry no API key, no agent code and no private data (S12)."""

from __future__ import annotations

import re
from pathlib import Path

DEPLOY = Path("deploy")
DOCKERFILES = [DEPLOY / "game.Dockerfile", DEPLOY / "dashboard.Dockerfile"]


def _copied_sources(text: str) -> list[str]:
    return [m.group(1) for m in re.finditer(r"^COPY\s+(\S+)", text, re.MULTILINE)]


ALLOWED_COPIES = {
    "game.Dockerfile": {"requirements.txt", "game/"},
    "dashboard.Dockerfile": {
        "requirements.txt", ".streamlit/", "prereg.yaml",
        "dashboard/", "stats/", "contextgraph/", "snapshot/",
    },
}


def test_images_copy_only_their_own_folders() -> None:
    for path in DOCKERFILES:
        copied = set(_copied_sources(path.read_text(encoding="utf-8")))
        assert copied <= ALLOWED_COPIES[path.name], (path, copied - ALLOWED_COPIES[path.name])


def test_no_api_key_or_agent_packages_in_images() -> None:
    for path in DOCKERFILES:
        text = path.read_text(encoding="utf-8")
        assert "ANTHROPIC_API_KEY" not in text, path
        installs = " ".join(re.findall(r"grep -E '([^']+)'", text))
        assert installs, path
        assert "anthropic" not in installs and "playwright" not in installs, path


def test_game_runs_one_worker_on_port_env() -> None:
    text = (DEPLOY / "game.Dockerfile").read_text(encoding="utf-8")
    assert "--workers 1" in text
    assert "--host 0.0.0.0" in text and "${PORT" in text


def test_dashboard_is_public_mode() -> None:
    text = (DEPLOY / "dashboard.Dockerfile").read_text(encoding="utf-8")
    assert "FAIRPLAY_MODE=public" in text
    assert "--server.address 0.0.0.0" in text and "${PORT" in text


def test_dockerignore_excludes_secrets_and_private_data() -> None:
    lines = set(Path(".dockerignore").read_text(encoding="utf-8").split())
    assert {".env", ".env.*", "data/", "oracle/"} <= lines
