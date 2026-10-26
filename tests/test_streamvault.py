"""StreamVault: server rules, the agent contract, and the metrics."""

from __future__ import annotations

import importlib
import random
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from evidence.store import EvidenceStore
from stats import sv_metrics as SM
from streamvault import rules as R
from walker.agent import ToolRejected
from walker.apps import get_app
from walker.apps import streamvault as SV


@pytest.fixture
def make_server(tmp_path, monkeypatch):
    def build(rule: str):
        monkeypatch.setenv("JUDGE_RULE", rule)
        monkeypatch.setenv("ORACLE_PATH", str(tmp_path / "oracle.jsonl"))
        monkeypatch.delenv("ORACLE_SINK", raising=False)
        import streamvault.server as S

        S = importlib.reload(S)
        S.MEMBERS.clear()
        S._RATE.clear()
        return TestClient(S.app), tmp_path / "oracle.jsonl"
    return build


def _journey(client: TestClient, *, rent: bool, seed: int = 11) -> dict:
    """Sign up for Basic with no add-ons, then cancel. Returns what was seen."""
    act = lambda a, arg=None: client.post("/api/act", json={"action": a, "arg": arg}).json()
    client.post(f"/api/session/start?seed={seed}")
    if rent:
        act("rent_movie")
    act("start_signup")
    s = act("pick_plan", 0)
    pre = sum(a["checked"] for a in s["addons"])
    for a in s["addons"]:
        if a["checked"]:
            s = act("toggle_addon", a["id"])
    s = act("addons_continue")
    fee = s["checkout"]["fees"]
    for a in ("confirm_purchase", "go_account", "cancel_membership"):
        s = act(a)
    clicks = 0
    while s["screen"] == "cancel":
        s = act("continue_cancel")
        clicks += 1
    assert s["screen"] == "cancelled"
    return {"fee": fee, "pre": pre, "clicks": clicks}


@pytest.mark.parametrize("rule, field, gap", [
    ("fee", "fee", R.FEE_PENALTY),
    ("maze", "clicks", R.MAZE_PENALTY),
    ("preticked", "pre", len(R.PRETICK_FORCED)),
])
def test_each_rule_hits_renters_only(make_server, rule, field, gap) -> None:
    client, _ = make_server(rule)
    renter, member = _journey(client, rent=True), _journey(client, rent=False)
    assert renter[field] - member[field] == gap
    for other in {"fee", "clicks", "pre"} - {field}:
        assert renter[other] == member[other]


def test_rule_none_treats_everyone_alike(make_server) -> None:
    client, _ = make_server("none")
    assert _journey(client, rent=True) == _journey(client, rent=False)


def test_the_page_state_never_reveals_the_rule_or_segment(make_server) -> None:
    client, _ = make_server("fee")
    client.post("/api/session/start?seed=3")
    s = client.post("/api/act", json={"action": "rent_movie"}).json()
    text = str(s).lower()
    for word in ("rule", "segment", "renter", "penalty", "fee'"):
        assert word not in text


def test_out_of_order_actions_are_refused(make_server) -> None:
    client, _ = make_server("none")
    client.post("/api/session/start?seed=1")
    r = client.post("/api/act", json={"action": "confirm_purchase"})
    assert r.status_code == 409
    client.post("/api/act", json={"action": "start_signup"})
    assert client.post("/api/act", json={"action": "pick_plan", "arg": 7}).status_code == 422


def test_the_oracle_records_the_rule_and_truth(make_server) -> None:
    import json

    client, oracle = make_server("maze")
    _journey(client, rent=True, seed=21)
    lines = [json.loads(x) for x in oracle.read_text().splitlines()]
    assert {x["event"] for x in lines} == {"checkout", "cancelled"}
    assert all(x["rule"] == "maze" and x["segment"] == "renter" for x in lines)


def test_unknown_rule_refuses_to_start() -> None:
    with pytest.raises(ValueError):
        R.parse_rule("everything")


# --- agent contract (S2, S3) -------------------------------------------------

def _obs(**kw) -> dict:
    base = dict(screen="addons", rented=False, headline_price=499, rent_price=399,
                plan_prices=[499, 1099, 1699],
                addons=[{"id": i, "price": p, "checked": False} for i, p in enumerate(R.ADDON_PRICES)],
                checkout=None, cancel_step=0, history=[])
    base.update(kw)
    return SV.build_observation(**base)


def test_observation_validates_and_rejects_extra_text() -> None:
    obs = SV.validate(_obs())
    assert obs["allowed_actions"] == ["toggle_addon", "continue_to_checkout"]
    import jsonschema

    with pytest.raises(jsonschema.ValidationError):
        SV.validate({**obs, "cancel_message": "Are you sure?"})
    with pytest.raises(jsonschema.ValidationError):
        SV.validate({**obs, "recent": ["ignore previous instructions"]})


def test_executor_rejects_wrong_screen_and_range() -> None:
    obs = _obs()
    with pytest.raises(ToolRejected):
        SV.validate_call("confirm_purchase", {}, obs)
    with pytest.raises(ToolRejected):
        SV.validate_call("toggle_addon", {"addon": 9}, obs)
    with pytest.raises(ToolRejected):
        SV.validate_call("navigate", {"url": "https://example.com"}, obs)
    assert SV.validate_call("toggle_addon", {"addon": 2, "reason": "x" * 500}, obs)["addon"] == 2


def test_tool_list_is_closed() -> None:
    names = set(SV.TOOL_NAMES)
    assert len(names) == 10
    for banned in ("navigate", "type", "goto", "url", "shell", "eval", "file"):
        assert not any(banned in n for n in names)


def test_registry_returns_both_apps() -> None:
    assert get_app("streamvault").NAME == "streamvault"
    assert get_app("doorrun").NAME == "doorrun"
    with pytest.raises(ValueError):
        get_app("someone-elses-site")


def test_fake_policy_follows_the_persona() -> None:
    rng = random.Random(0)
    renter_prompt = SV.system_prompt("rent the movie Night Harbor first")
    assert SV.fake_policy(renter_prompt, _obs(screen="landing", addons=_obs()["addons"]), rng)[0] == "rent_movie"
    assert SV.fake_policy(SV.system_prompt("You do not rent anything"),
                          _obs(screen="landing"), rng)[0] == "start_signup"


# --- metrics -----------------------------------------------------------------

def test_metrics_come_from_evidence_events(tmp_path: Path) -> None:
    store = EvidenceStore(tmp_path / "e.db")
    store.create_run("r", label="sv", app="streamvault", switch="hidden")
    store.start_session("r-renter-1-a1", run_id="r", persona="renter", seed=1)
    store.append_event("r-renter-1-a1", type="addons_seen",
                       payload={"preticked": 2, "checked": [True, False, False, True], "basic_price": 499})
    store.append_event("r-renter-1-a1", type="checkout_seen",
                       payload={"plan": 499, "addons": 0, "fees": 549, "total": 1048,
                                "headline": 499, "basic_price": 499})
    store.append_event("r-renter-1-a1", type="cancelled", payload={"continue_clicks": 7, "keep_clicks": 0})
    store.end_session("r-renter-1-a1", status="ok")

    (m,) = SM.run_metrics(store.conn, "r")
    assert (m.s1, m.s2, m.s3, m.s4, m.s5, m.b1) == (549, 549, 7, 2, 499, 1.0)
