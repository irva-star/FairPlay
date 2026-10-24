"""Tool executor and agent loop (SPEC section 6).

Our code is the agent's eyes and hands. It reads typed state from the page,
asks the model for exactly one tool call, checks that call against the actions
the current screen allows, and only then clicks. The model never navigates and
never types.

The loop waits on the page's render counter (data-seq) after every click,
because data-state stays "level" from one level to the next and so cannot tell
us that a new level has loaded.
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
from typing import Any

from walker import observe
from walker.budget import Usage, cost_usd
from walker.prompts import REASON_MAX_CHARS, TOOL_NAMES, system_prompt

MAX_STEPS = 30
SEQ_TIMEOUT_SECONDS = 10.0

# Which element each tool clicks. pick_door is positional, so it is built below.
STATIC_TEST_IDS = {
    "open_store": "open-store",
    "buy_item": "buy-gold-frame",
    "close_store": "close-store",
    "dismiss_offer": "offer-dismiss",
}


class ToolRejected(ValueError):
    """The model asked for something outside the contract. Never improvise."""

    def __init__(self, message: str, error: str) -> None:
        super().__init__(message)
        self.error = error  # one of observe.ERRORS


def validate_call(tool: str | None, args: dict[str, Any], observation: dict) -> dict[str, Any]:
    """Check a tool call against the fixed list and the current screen (S3)."""
    if not tool or tool not in TOOL_NAMES:
        raise ToolRejected(f"unknown tool {tool!r}", observe.ERRORS[0])

    allowed = observation.get("allowed_actions") or []
    if tool not in allowed:
        raise ToolRejected(f"{tool} not allowed on screen {observation.get('screen')!r}",
                           observe.ERRORS[1])

    clean: dict[str, Any] = {}
    reason = args.get("reason")
    clean["reason"] = str(reason)[:REASON_MAX_CHARS] if reason is not None else ""

    if tool == "pick_door":
        door = args.get("door")
        if not isinstance(door, int) or isinstance(door, bool) or not 0 <= door <= 2:
            raise ToolRejected(f"door out of range: {door!r}", observe.ERRORS[2])
        ids = {d["id"] for d in observation.get("doors") or []}
        if door not in ids:
            raise ToolRejected(f"door {door} is not on this level", observe.ERRORS[2])
        clean["door"] = door

    if tool == "buy_item":
        item = args.get("item_id")
        if item != "gold_frame":
            raise ToolRejected(f"unknown item {item!r}", observe.ERRORS[2])
        clean["item_id"] = item

    return clean


def target_test_id(tool: str, args: dict[str, Any]) -> str:
    if tool == "pick_door":
        return f"door-{args['door']}"
    return STATIC_TEST_IDS[tool]


def history_entry(tool: str, args: dict[str, Any]) -> str | None:
    """Harness-built action string for `recent`. pick_door waits for its result."""
    if tool == "pick_door":
        return None
    if tool == "buy_item":
        return "buy_item gold_frame"
    return tool


async def wait_for_seq(page, previous: int, timeout: float = SEQ_TIMEOUT_SECONDS) -> int:
    """Wait for the client to finish rendering the next response."""
    deadline = asyncio.get_running_loop().time() + timeout
    while True:
        current = await observe.read_seq(page)
        if current > previous:
            return current
        if asyncio.get_running_loop().time() >= deadline:
            raise TimeoutError(f"data-seq did not rise above {previous} within {timeout}s")
        await asyncio.sleep(0.05)


@dataclass
class SessionResult:
    session_id: str
    persona: str
    seed: int
    status: str = "ok"
    steps: int = 0
    purchases: int = 0
    cost_usd: float = 0.0
    compliant: bool = True
    notes: list[str] = field(default_factory=list)


@dataclass
class SessionDeps:
    """Everything the loop needs from the outside, so it can be faked in tests."""
    llm: Any
    emit: Any            # async (type, payload, *, level_no=None, screenshot_id=None) -> None
    capture: Any         # async (label) -> screenshot_id | None
    prices: Any
    budget: Any = None


async def run_session(
    page,
    *,
    session_id: str,
    persona: str,
    persona_sentence: str,
    seed: int,
    deps: SessionDeps,
    max_steps: int = MAX_STEPS,
    run_id: str = "",
) -> SessionResult:
    result = SessionResult(session_id=session_id, persona=persona, seed=seed)
    prompt = system_prompt(persona_sentence)
    history: list[str] = []
    pending: dict[str, Any] | None = None
    error: str | None = None
    retried = False
    prev_screen: str | None = None

    await deps.emit("session_start", {"persona": persona, "seed": seed, "run_id": run_id})
    seq = await observe.read_seq(page)
    previous_result = observe.RESULTS[2]  # NONE

    for _ in range(max_steps):
        obs = await observe.read_state(page, history, error)
        error = None

        # Resolve the previous pick now that the page reports its outcome. This
        # also runs on the final read, where screen is "done", so the last
        # level's result is never lost.
        if pending is not None and obs["last_result"] in ("WIN", "LOSS"):
            history.append(f"pick_door {pending['door']} -> {obs['last_result']}")
            await deps.emit("level_end", {
                "level": pending["level"],
                "result": obs["last_result"],
                "door": pending["door"],
                "chosen_chance": pending["chosen_chance"],
                "best_chance": pending["best_chance"],
                "chose_best": pending["chose_best"],
            }, level_no=pending["level"])
            pending = None

        if obs["screen"] == "level" and obs["last_result"] == "LOSS":
            # M2 is measured here: the level that follows a loss.
            await deps.emit("level_start", {
                "level": obs["level"],
                "follows_loss": True,
                "best_chance": max(d["win_chance"] for d in obs["doors"]),
            }, level_no=obs["level"])

        if obs["screen"] == "offer" and prev_screen != "offer":
            await deps.emit("offer_shown", {
                "level": obs["level"], "price": obs["offer_price"],
            }, level_no=obs["level"])

        if obs["screen"] == "offer":
            await deps.capture("offer")
        elif obs["screen"] == "level" and previous_result == "LOSS":
            await deps.capture("post_loss_level")
        previous_result = obs["last_result"]
        prev_screen = obs["screen"]

        if obs["screen"] == "done":
            break

        if deps.budget is not None:
            if deps.budget.session_over_cap(session_id):
                result.status = "session_cap_reached"
                result.notes.append("per-session spend cap reached")
                break

        decision = await deps.llm.decide(prompt, obs)
        usage: Usage = decision.usage
        step_cost = 0.0
        if deps.budget is not None:
            step_cost = deps.budget.record(usage, session_id=session_id,
                                           model=decision.model, run_id=run_id)
        else:
            step_cost = cost_usd(usage, deps.prices)
        result.cost_usd += step_cost
        result.steps += 1

        rejected: ToolRejected | None = None
        clean: dict[str, Any] = {}
        if decision.invalid_reason is not None:
            rejected = ToolRejected(decision.invalid_reason, observe.ERRORS[0])
        else:
            try:
                clean = validate_call(decision.tool, decision.args, obs)
            except ToolRejected as exc:
                rejected = exc

        await deps.emit("agent_step", {
            "observation": obs,
            "tool": decision.tool,
            "args": decision.args,
            "reason": clean.get("reason", ""),
            "stop_reason": decision.stop_reason,
            "input_tokens": usage.input_tokens,
            "output_tokens": usage.output_tokens,
            "cache_creation_input_tokens": usage.cache_creation_input_tokens,
            "cache_read_input_tokens": usage.cache_read_input_tokens,
            "usd": step_cost,
            "latency_ms": decision.latency_ms,
            "model": decision.model,
            "rejected": None if rejected is None else str(rejected),
        })

        if rejected is not None:
            if retried:
                result.status = "protocol_deviation"
                result.notes.append(f"second invalid call: {rejected}")
                await deps.emit("protocol_deviation", {"detail": str(rejected)})
                break
            retried = True
            error = rejected.error
            continue

        retried = False
        tool = decision.tool
        assert tool is not None

        await page.get_by_test_id(target_test_id(tool, clean)).click()

        if tool == "pick_door":
            chances = {d["id"]: d["win_chance"] for d in obs["doors"]}
            best = max(chances.values())
            chosen = chances[clean["door"]]
            pending = {
                "level": obs["level"],
                "door": clean["door"],
                "chosen_chance": chosen,
                "best_chance": best,
                "chose_best": chosen == best,
            }
        else:
            entry = history_entry(tool, clean)
            if entry:
                history.append(entry)

        if tool == "buy_item":
            result.purchases += 1
            await deps.emit("purchase", {"item_id": clean["item_id"], "level": obs["level"]})
        elif tool == "open_store":
            await deps.emit("store_open", {"level": obs["level"]})
        elif tool == "close_store":
            await deps.emit("store_close", {"level": obs["level"]})
        elif tool == "dismiss_offer":
            await deps.emit("offer_dismissed", {"level": obs["level"]}, level_no=obs["level"])

        try:
            seq = await wait_for_seq(page, seq)
        except TimeoutError as exc:
            result.status = "render_timeout"
            result.notes.append(str(exc))
            await deps.emit("error", {"detail": str(exc)})
            break
    else:
        result.status = "step_limit"
        result.notes.append(f"hit the {max_steps}-step limit")

    await deps.capture("session_end")
    result.compliant = check_compliance(persona, result.purchases)
    if not result.compliant:
        result.notes.append(f"{persona} made {result.purchases} purchases")
    await deps.emit("session_end", {
        "status": result.status,
        "steps": result.steps,
        "purchases": result.purchases,
        "compliant": result.compliant,
        "usd": result.cost_usd,
        "notes": result.notes,
    })
    return result


EXPECTED_PURCHASES = {"spender": 1, "free": 0, "free_twin": 0}


def check_compliance(persona: str, purchases: int) -> bool:
    """Non-compliant sessions are flagged, never hidden."""
    return purchases == EXPECTED_PURCHASES.get(persona, 0)
