"""The hosted hub: one origin for the landing page, dashboard and both apps."""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from hub.server import app


@pytest.fixture
def client() -> TestClient:
    return TestClient(app, base_url="https://testserver")


@pytest.mark.parametrize("path", ["/", "/dashboard.html", "/dashboard.js", "/style.css",
                                  "/healthz", "/doorrun/", "/doorrun/static/game.js",
                                  "/streamvault/static/client.js"])
def test_every_page_is_served(client: TestClient, path: str) -> None:
    assert client.get(path).status_code == 200


@pytest.mark.parametrize("bare", ["/doorrun", "/streamvault"])
def test_app_paths_get_their_trailing_slash(client: TestClient, bare: str) -> None:
    r = client.get(bare, follow_redirects=False)
    assert r.status_code == 307 and r.headers["location"] == f"{bare}/"


def test_the_apps_use_page_relative_paths() -> None:
    """Absolute /api or /static paths would escape the hub's mount."""
    js = Path("game/static/game.js").read_text(encoding="utf-8")
    assert '"/api/' not in js and "`/api/" not in js
    for page in ("game/static/index.html", "game/static/index_rpg.html", "streamvault/static/index.html"):
        assert '"/static/' not in Path(page).read_text(encoding="utf-8"), page


def test_the_landing_page_carries_the_demo_banner(client: TestClient) -> None:
    for page in ("/", "/dashboard.html"):
        assert "TEST GAME, planted rule (demo)" in client.get(page).text


def test_the_dashboard_json_never_holds_a_full_approver_name() -> None:
    doc = Path("hub/static/dashboard.json")
    if not doc.exists():
        pytest.skip("run scripts/export_dashboard.py first")
    text = doc.read_text(encoding="utf-8")
    assert "sk-ant-" not in text
    assert "approver" not in json.loads(text)  # approvals are not exported at all
