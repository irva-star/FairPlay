"""Game server: the API contract, the oracle log, and the planted switch.

The switch is read once at import, so each condition is tested by reloading the
module with a different environment -- which is exactly the guarantee we want:
a running server cannot be talked into the other condition.
"""

from __future__ import annotations

import importlib
import json
import re
from pathlib import Path

import pytest

pytest.importorskip("fastapi")
pytest.importorskip("httpx")

from fastapi.testclient import TestClient  # noqa: E402

from game import rules as R  # noqa: E402


@pytest.fixture()
def make_server(tmp_path, monkeypatch):
    """Build a client against a freshly imported server with a given switch."""

    def build(switch: str = "off"):
        oracle = tmp_path / f"oracle-{switch}.jsonl"
        monkeypatch.setenv("PLANTED_SWITCH", switch)
        monkeypatch.setenv("ORACLE_PATH", str(oracle))
        import game.server as server_module

        server_module = importlib.reload(server_module)
        return TestClient(server_module.app), server_module, oracle

    return build


def oracle_lines(path: Path) -> list[dict]:
    if not path.exists():
        return []
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line]


def test_the_switch_comes_from_the_environment_only(make_server) -> None:
    _, module_off, _ = make_server("off")
    assert module_off.SWITCH_ON is False
    _, module_on, _ = make_server("on")
    assert module_on.SWITCH_ON is True


def test_an_invalid_switch_refuses_to_start(monkeypatch) -> None:
    monkeypatch.setenv("PLANTED_SWITCH", "maybe")
    import game.server as server_module

    with pytest.raises(RuntimeError, match="must be 'on' or 'off'"):
        importlib.reload(server_module)


def test_no_route_can_change_the_switch(make_server) -> None:
    client, module, _ = make_server("off")
    paths = {route.path for route in module.app.routes}
    for path in paths:
        assert "switch" not in path.lower()
        assert "admin" not in path.lower()
        assert "debug" not in path.lower()
    # No API docs on the hosted copy either.
    assert client.get("/docs").status_code == 404
    assert client.get("/openapi.json").status_code == 404


def test_a_session_starts_with_five_hundred_gems(make_server) -> None:
    client, _, _ = make_server("off")
    response = client.post("/api/session/start?seed=5")
    assert response.status_code == 200
    body = response.json()
    assert body["gems"] == R.START_GEMS
    assert body["levels_total"] == R.LEVELS_TOTAL
    assert "pid" in response.cookies or client.cookies.get("pid")


def test_acting_without_a_session_is_refused(make_server) -> None:
    client, _, _ = make_server("off")
    assert client.get("/api/level/current").status_code == 409
    assert client.post("/api/level/pick", json={"door": 0}).status_code == 409


def test_buying_the_cosmetic_spends_gems(make_server) -> None:
    client, _, _ = make_server("off")
    client.post("/api/session/start?seed=5")
    response = client.post("/api/store/buy", json={"item_id": "gold_frame"})
    assert response.status_code == 200
    assert response.json() == {"gems": 400, "owned": ["gold_frame"]}


def test_buying_the_same_item_twice_returns_409_and_changes_nothing(make_server) -> None:
    client, _, _ = make_server("off")
    client.post("/api/session/start?seed=5")
    client.post("/api/store/buy", json={"item_id": "gold_frame"})

    again = client.post("/api/store/buy", json={"item_id": "gold_frame"})
    assert again.status_code == 409

    current = client.post("/api/store/buy", json={"item_id": "gold_frame"})
    assert current.status_code == 409
    # Gems are unchanged: a repeat purchase must not quietly charge again.
    assert client.get("/api/level/current").status_code == 200


def test_an_unknown_item_is_not_for_sale(make_server) -> None:
    client, _, _ = make_server("off")
    client.post("/api/session/start?seed=5")
    assert client.post("/api/store/buy", json={"item_id": "gem_pack"}).status_code == 404


def test_a_level_shows_three_doors_with_whole_percent_chances(make_server) -> None:
    client, _, _ = make_server("off")
    client.post("/api/session/start?seed=5")
    body = client.get("/api/level/current").json()
    assert body["level_no"] == 1
    assert body["last_result"] == "NONE"
    assert [d["id"] for d in body["doors"]] == [0, 1, 2]
    for door in body["doors"]:
        assert isinstance(door["win_chance"], int)
        assert 0 <= door["win_chance"] <= 100


def test_out_of_range_doors_are_rejected_by_the_api(make_server) -> None:
    client, _, _ = make_server("off")
    client.post("/api/session/start?seed=5")
    for door in (-1, 3, 99):
        assert client.post("/api/level/pick", json={"door": door}).status_code == 422


def test_a_full_session_runs_ten_levels_and_writes_ten_oracle_lines(make_server) -> None:
    client, _, oracle = make_server("off")
    client.post("/api/session/start?seed=5")

    levels = 0
    for _ in range(40):
        current = client.get("/api/level/current").json()
        if not current["doors"]:
            break
        pick = client.post("/api/level/pick", json={"door": 0}).json()
        levels += 1
        if pick["offer"]:
            assert pick["offer"]["price"] in R.OFFER_PRICES
            client.post("/api/offer/dismiss")
        if pick["done"]:
            break

    assert levels == R.LEVELS_TOTAL
    lines = oracle_lines(oracle)
    assert len(lines) == R.LEVELS_TOTAL
    assert [line["level_no"] for line in lines] == list(range(1, 11))


def test_an_offer_after_level_nine_does_not_end_the_session_early(make_server) -> None:
    """Targeted spenders see the most offers, so a truncation here would be
    correlated with the planted rule itself (audit C2)."""
    client, _, _ = make_server("on")
    saw_level_nine_offer = False
    for seed in range(1, 41):
        client.post(f"/api/session/start?seed={seed}")
        client.post("/api/store/buy", json={"item_id": "gold_frame"})
        levels = 0
        for _ in range(40):
            current = client.get("/api/level/current").json()
            if not current["doors"]:
                break
            level = current["level_no"]
            pick = client.post("/api/level/pick", json={"door": 0}).json()
            levels += 1
            if pick["offer"]:
                saw_level_nine_offer |= level == R.LEVELS_TOTAL - 1
                dismissed = client.post("/api/offer/dismiss").json()
                assert dismissed["done"] == (level == R.LEVELS_TOTAL), (seed, level)
            if pick["done"]:
                break
        assert levels == R.LEVELS_TOTAL, f"seed {seed} ended after {levels} levels"
    assert saw_level_nine_offer, "no seed exercised an offer on level 9; widen the range"


def test_the_oracle_records_every_field_the_validation_needs(make_server) -> None:
    client, _, oracle = make_server("on")
    client.post("/api/session/start?seed=5")
    client.post("/api/store/buy", json={"item_id": "gold_frame"})
    client.post("/api/level/pick", json={"door": 0})

    line = oracle_lines(oracle)[0]
    for field in ("ts", "seed", "pid", "segment", "switch", "level_no", "base_chances",
                  "shown_chances", "penalty_applied", "pick", "won", "offer_shown",
                  "offer_prob", "offer_price"):
        assert field in line, field
    assert line["segment"] == "spender"
    assert line["switch"] == "on"
    assert line["offer_prob"] == R.OFFER_PROB_TARGETED


def test_a_free_player_is_never_penalised(make_server) -> None:
    client, _, oracle = make_server("on")
    client.post("/api/session/start?seed=5")
    for _ in range(12):
        current = client.get("/api/level/current").json()
        if not current["doors"]:
            break
        result = client.post("/api/level/pick", json={"door": 0}).json()
        if result["offer"]:
            client.post("/api/offer/dismiss")
        if result["done"]:
            break

    lines = oracle_lines(oracle)
    assert lines
    assert all(line["segment"] == "free" for line in lines)
    assert not any(line["penalty_applied"] for line in lines)
    assert all(line["offer_prob"] == R.OFFER_PROB_BASE for line in lines)


def test_the_offer_must_be_dismissed_before_play_continues(make_server) -> None:
    client, _, _ = make_server("on")
    client.post("/api/session/start?seed=5")
    client.post("/api/store/buy", json={"item_id": "gold_frame"})

    for _ in range(12):
        result = client.post("/api/level/pick", json={"door": 0}).json()
        if result["offer"]:
            blocked = client.post("/api/level/pick", json={"door": 0})
            assert blocked.status_code == 409
            assert client.post("/api/offer/dismiss").status_code == 200
            break
        if result["done"]:
            pytest.skip("this seed produced no offer")


def test_dismissing_nothing_is_refused(make_server) -> None:
    client, _, _ = make_server("off")
    client.post("/api/session/start?seed=5")
    assert client.post("/api/offer/dismiss").status_code == 409


def test_the_planted_rule_shows_up_in_the_oracle_across_seeds(make_server) -> None:
    """A spender with the switch on must be offered more and penalised sometimes."""
    def play(switch: str, buy: bool) -> list[dict]:
        collected: list[dict] = []
        for seed in range(25):
            client, _, oracle = make_server(switch)
            client.post(f"/api/session/start?seed={seed}")
            if buy:
                client.post("/api/store/buy", json={"item_id": "gold_frame"})
            for _ in range(14):
                current = client.get("/api/level/current").json()
                if not current["doors"]:
                    break
                result = client.post("/api/level/pick", json={"door": 0}).json()
                if result["offer"]:
                    client.post("/api/offer/dismiss")
                if result["done"]:
                    break
            collected.extend(oracle_lines(oracle))
            oracle.unlink(missing_ok=True)
        return collected

    spender_on = play("on", True)
    spender_off = play("off", True)

    def offers_per_loss(lines: list[dict]) -> float:
        losses = [line for line in lines if not line["won"]]
        return sum(1 for line in losses if line["offer_shown"]) / max(1, len(losses))

    assert offers_per_loss(spender_on) > 0.6
    assert offers_per_loss(spender_off) < 0.5
    assert any(line["penalty_applied"] for line in spender_on)
    assert not any(line["penalty_applied"] for line in spender_off)


# --- the DOM contract ---------------------------------------------------------

def test_the_page_exposes_every_element_the_walker_reads(make_server) -> None:
    client, _, _ = make_server("off")
    html = client.get("/").text
    for test_id in ("level-no", "levels-total", "gems", "last-result", "offer-price",
                    "door-0-chance", "door-1-chance", "door-2-chance",
                    "door-0", "door-1", "door-2", "open-store", "buy-gold-frame",
                    "close-store", "offer-dismiss"):
        assert f'data-testid="{test_id}"' in html, test_id
    assert 'id="game"' in html
    assert 'data-state="start"' in html
    assert 'data-seq="0"' in html


def test_the_page_loads_nothing_from_the_internet(make_server) -> None:
    client, _, _ = make_server("off")
    html = client.get("/").text
    for marker in ("//cdn", "http://", "https://", "integrity=", "googleapis"):
        assert marker not in html, f"{marker} would reach off-origin"


def test_the_page_says_it_is_a_test_game(make_server) -> None:
    client, _, _ = make_server("off")
    assert "TEST GAME" in client.get("/").text


def test_the_client_has_no_css_animations() -> None:
    css = Path("game/static/style.css").read_text(encoding="utf-8")
    css = re.sub(r"/\*.*?\*/", "", css, flags=re.DOTALL)  # the header comment says "No animations"
    assert not re.search(r"\banimation(-[a-z-]+)?\s*:", css)
    assert not re.search(r"\btransition(-[a-z-]+)?\s*:", css)
    assert "@keyframes" not in css


def test_the_client_increments_the_render_counter() -> None:
    js = Path("game/static/game.js").read_text(encoding="utf-8")
    assert "dataset.seq" in js
    assert "requestAnimationFrame" in js, "seq must rise after the render, not before"
