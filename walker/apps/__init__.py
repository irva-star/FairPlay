"""App adapters. Each audited app supplies its own agent contract:

    NAME, TOOLS, system_prompt_hash(), tools_hash(), run_session(...), fake_policy

The engine (budget, rate limiter, guard, evidence writer, CLI) is shared. Adding
an app means writing one adapter; it never means loosening the guard, and the
browser still reaches only the origins in walker/guard.py.
"""

from __future__ import annotations

from types import SimpleNamespace

APP_NAMES = ("doorrun", "streamvault")


def get_app(name: str):
    if name == "doorrun":
        from walker import agent, prompts

        return SimpleNamespace(
            NAME="doorrun", TOOLS=prompts.TOOLS,
            system_prompt_hash=prompts.system_prompt_hash, tools_hash=prompts.tools_hash,
            run_session=agent.run_session, fake_policy=_doorrun_fake_policy,
        )
    if name == "streamvault":
        from walker.apps import streamvault

        return streamvault
    raise ValueError(f"unknown app {name!r}; expected one of {APP_NAMES}")


def _doorrun_fake_policy(system_prompt_text: str, obs: dict, rng):
    """Zero-cost stand-in for the model on Door Run, for development runs only.

    Follows the persona sentence (the spender buys once, free players don't),
    picks the best door 85% of the time and always dismisses offers.
    """
    allowed = obs.get("allowed_actions") or []
    if not allowed:
        return None, {}
    screen = obs["screen"]
    if screen == "start":
        return "open_store", {}
    if screen == "store":
        wants = "buy the gold frame once" in system_prompt_text.lower()
        bought = "buy_item gold_frame" in (obs.get("recent") or [])
        if wants and not bought:
            return "buy_item", {"item_id": "gold_frame"}
        return "close_store", {}
    if screen == "level":
        doors = obs["doors"]
        best = max(doors, key=lambda d: d["win_chance"])["id"]
        return "pick_door", {"door": best if rng.random() < 0.85 else rng.choice([0, 1, 2])}
    if screen == "offer":
        return "dismiss_offer", {}
    return allowed[0], {}
