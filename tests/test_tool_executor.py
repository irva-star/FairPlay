"""Rule S3: the agent can only take safe actions, from a closed list."""

from __future__ import annotations

import pytest

from walker import observe
from walker.agent import ToolRejected, target_test_id, validate_call
from walker.prompts import REASON_MAX_CHARS, TOOL_NAMES, TOOLS


def level_obs() -> dict:
    return observe.build_observation(
        screen="level", level=3, levels_total=10, gems=500,
        doors=[{"id": 0, "win_chance": 60}, {"id": 1, "win_chance": 45},
               {"id": 2, "win_chance": 70}],
        last_result="NONE", offer_price=None, history=[],
    )


def store_obs() -> dict:
    return observe.build_observation(
        screen="store", level=1, levels_total=10, gems=500, doors=[],
        last_result="NONE", offer_price=None, history=[],
    )


def test_the_tool_list_is_exactly_five_and_has_no_escape_hatches() -> None:
    assert TOOL_NAMES == ("open_store", "buy_item", "close_store", "pick_door", "dismiss_offer")
    forbidden = ("navigate", "goto", "url", "type", "fill", "press", "eval", "exec",
                 "shell", "bash", "read_file", "write_file", "fetch", "request",
                 "screenshot", "accept_offer", "buy_gems")
    for tool in TOOLS:
        name = tool["name"]
        for word in forbidden:
            assert word not in name, f"tool {name!r} looks like an escape hatch"
        # No tool may take a free-form string other than the capped reason.
        for arg, spec in tool["input_schema"]["properties"].items():
            if arg == "reason":
                assert spec["maxLength"] == REASON_MAX_CHARS
            elif spec.get("type") == "string":
                assert "enum" in spec, f"{name}.{arg} is unconstrained free text"


def test_there_is_no_accept_offer_tool() -> None:
    """Agents may only dismiss, so play stays comparable between personas."""
    assert "accept_offer" not in TOOL_NAMES


@pytest.mark.parametrize("tool", ["navigate", "type_text", "run_shell", "", None, "Pick_Door"])
def test_tools_outside_the_list_are_rejected(tool) -> None:
    with pytest.raises(ToolRejected) as excinfo:
        validate_call(tool, {"reason": "x"}, level_obs())
    assert excinfo.value.error in observe.ERRORS


def test_tool_not_allowed_on_this_screen_is_rejected() -> None:
    with pytest.raises(ToolRejected) as excinfo:
        validate_call("buy_item", {"item_id": "gold_frame", "reason": "x"}, level_obs())
    assert excinfo.value.error == observe.ERRORS[1]

    with pytest.raises(ToolRejected):
        validate_call("pick_door", {"door": 0, "reason": "x"}, store_obs())


@pytest.mark.parametrize("door", [-1, 3, 99, "1", 1.0, None, True])
def test_out_of_range_door_is_rejected(door) -> None:
    with pytest.raises(ToolRejected) as excinfo:
        validate_call("pick_door", {"door": door, "reason": "x"}, level_obs())
    assert excinfo.value.error == observe.ERRORS[2]


def test_valid_door_is_accepted_and_mapped_to_a_test_id() -> None:
    clean = validate_call("pick_door", {"door": 2, "reason": "best odds"}, level_obs())
    assert clean == {"reason": "best odds", "door": 2}
    assert target_test_id("pick_door", clean) == "door-2"


def test_unknown_item_is_rejected() -> None:
    with pytest.raises(ToolRejected):
        validate_call("buy_item", {"item_id": "gem_pack_99", "reason": "x"}, store_obs())


def test_the_only_purchasable_item_is_the_cosmetic() -> None:
    clean = validate_call("buy_item", {"item_id": "gold_frame", "reason": "persona"}, store_obs())
    assert clean["item_id"] == "gold_frame"
    assert target_test_id("buy_item", clean) == "buy-gold-frame"


def test_reason_is_truncated_not_trusted() -> None:
    long_reason = "x" * 500
    clean = validate_call("pick_door", {"door": 0, "reason": long_reason}, level_obs())
    assert len(clean["reason"]) == REASON_MAX_CHARS


def test_missing_reason_is_tolerated() -> None:
    clean = validate_call("pick_door", {"door": 1}, level_obs())
    assert clean["reason"] == ""


def test_unexpected_arguments_are_dropped_not_forwarded() -> None:
    clean = validate_call(
        "pick_door",
        {"door": 1, "reason": "ok", "url": "https://example.com", "cmd": "rm -rf /"},
        level_obs(),
    )
    assert set(clean) == {"door", "reason"}


def test_every_tool_maps_to_a_test_id() -> None:
    assert target_test_id("open_store", {}) == "open-store"
    assert target_test_id("close_store", {}) == "close-store"
    assert target_test_id("dismiss_offer", {}) == "offer-dismiss"
    for door in range(3):
        assert target_test_id("pick_door", {"door": door}) == f"door-{door}"
