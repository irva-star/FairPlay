"""Build the typed observation the model is allowed to see (rule S2).

No HTML, page text, URL or screenshot ever reaches the model. Our code reads a
handful of named nodes, parses them strictly, and hands the model integers and
fixed enums only.

Strictness is the whole point. A numeric node must contain digits and nothing
else; last-result must be one of three words. If a hostile page put a sentence
into a label, parsing raises here and the session is aborted, so the text never
reaches the model. That is what makes rules S2 and S4 hold.

`validate()` imports jsonschema lazily so the pure parsing helpers above it stay
importable (and testable) before dependencies are installed.
"""

from __future__ import annotations

import re
from typing import Any

SCREENS = ("start", "store", "level", "offer", "done")
RESULTS = ("WIN", "LOSS", "NONE")
ACTIONS = ("open_store", "buy_item", "close_store", "pick_door", "dismiss_offer")

# Which tools are legal on which screen. The executor checks against this too.
ALLOWED_BY_SCREEN: dict[str, tuple[str, ...]] = {
    "start": ("open_store",),
    "store": ("buy_item", "close_store"),
    "level": ("pick_door",),
    "offer": ("dismiss_offer",),
    "done": (),
}

# Every string in `recent` is built by the harness, never by the page or model.
RECENT_PATTERN = r"^(open_store|close_store|buy_item gold_frame|dismiss_offer|pick_door [0-2] -> (WIN|LOSS))$"
_RECENT_RE = re.compile(RECENT_PATTERN)
RECENT_MAX = 3

# Fixed set of one-line errors for the single retry. An enum, so a retry can
# never become a channel for free text.
ERRORS = (
    "last call was not a valid tool call; call one of the allowed actions",
    "last call used an action that is not allowed on this screen",
    "last call had an out-of-range argument",
)

NUMERIC_TESTIDS = (
    "level-no", "gems", "door-0-chance", "door-1-chance", "door-2-chance", "offer-price",
)

_DIGITS_RE = re.compile(r"^[0-9]{1,6}$")


class ObservationError(ValueError):
    """The page did not match the DOM contract. Abort rather than improvise."""


def parse_int(raw: str | None, *, field: str) -> int:
    if raw is None:
        raise ObservationError(f"{field}: missing")
    text = raw.strip()
    if not _DIGITS_RE.match(text):
        # Deliberately does not echo the offending text into the message.
        raise ObservationError(f"{field}: expected digits only, got {len(text)} chars of other content")
    return int(text)


def parse_result(raw: str | None) -> str:
    text = (raw or "").strip()
    if text not in RESULTS:
        raise ObservationError("last-result: not one of WIN, LOSS, NONE")
    return text


def parse_screen(raw: str | None) -> str:
    # An attribute set by our own client: no whitespace tolerance, no normalising.
    text = raw or ""
    if text not in SCREENS:
        raise ObservationError(f"data-state: not one of {SCREENS}")
    return text


def format_recent(history: list[str]) -> list[str]:
    """Keep the last few harness-built action strings, dropping anything odd."""
    kept = [h for h in history if _RECENT_RE.match(h)]
    return kept[-RECENT_MAX:]


def build_observation(
    *,
    screen: str,
    level: int,
    levels_total: int,
    gems: int,
    doors: list[dict[str, int]],
    last_result: str,
    offer_price: int | None,
    history: list[str],
    error: str | None = None,
) -> dict[str, Any]:
    if screen not in SCREENS:
        raise ObservationError(f"screen: {screen!r}")
    if last_result not in RESULTS:
        raise ObservationError(f"last_result: {last_result!r}")
    if error is not None and error not in ERRORS:
        raise ObservationError("error: not one of the fixed harness errors")

    obs: dict[str, Any] = {
        "screen": screen,
        "level": level,
        "levels_total": levels_total,
        "gems": gems,
        "doors": doors if screen == "level" else [],
        "last_result": last_result,
        "offer_price": offer_price if screen == "offer" else None,
        "allowed_actions": list(ALLOWED_BY_SCREEN[screen]),
        "recent": format_recent(history),
    }
    if error is not None:
        obs["error"] = error
    return obs


OBSERVATION_SCHEMA: dict[str, Any] = {
    "$schema": "https://json-schema.org/draft/2020-12/schema",
    "type": "object",
    "additionalProperties": False,
    "required": [
        "screen", "level", "levels_total", "gems", "doors",
        "last_result", "offer_price", "allowed_actions", "recent",
    ],
    "properties": {
        "screen": {"enum": list(SCREENS)},
        "level": {"type": "integer", "minimum": 0, "maximum": 100},
        "levels_total": {"type": "integer", "minimum": 1, "maximum": 100},
        "gems": {"type": "integer", "minimum": 0, "maximum": 1_000_000},
        "doors": {
            "type": "array",
            "maxItems": 3,
            "items": {
                "type": "object",
                "additionalProperties": False,
                "required": ["id", "win_chance"],
                "properties": {
                    "id": {"type": "integer", "minimum": 0, "maximum": 2},
                    "win_chance": {"type": "integer", "minimum": 0, "maximum": 100},
                },
            },
        },
        "last_result": {"enum": list(RESULTS)},
        "offer_price": {"type": ["integer", "null"], "minimum": 0, "maximum": 10_000},
        "allowed_actions": {
            "type": "array",
            "maxItems": 2,
            "items": {"enum": list(ACTIONS)},
        },
        "recent": {
            "type": "array",
            "maxItems": RECENT_MAX,
            "items": {"type": "string", "pattern": RECENT_PATTERN},
        },
        "error": {"enum": list(ERRORS)},
    },
    "allOf": [
        {
            "if": {"properties": {"screen": {"const": "level"}}, "required": ["screen"]},
            "then": {"properties": {"doors": {"minItems": 3}}},
            "else": {"properties": {"doors": {"maxItems": 0}}},
        }
    ],
}


def validate(obs: dict[str, Any]) -> dict[str, Any]:
    """Structural check before every model call. Raises on anything unexpected."""
    import jsonschema  # lazy: keeps the parsers above importable without deps

    jsonschema.validate(obs, OBSERVATION_SCHEMA)
    return obs


async def read_state(page, history: list[str], error: str | None = None) -> dict[str, Any]:
    """Read the DOM contract and return a validated observation."""

    async def text(testid: str) -> str | None:
        node = page.get_by_test_id(testid)
        if await node.count() == 0:
            return None
        return await node.first.text_content()

    root = page.locator("#game")
    screen = parse_screen(await root.get_attribute("data-state"))

    doors: list[dict[str, int]] = []
    if screen == "level":
        doors = [
            {"id": i, "win_chance": parse_int(await text(f"door-{i}-chance"), field=f"door-{i}-chance")}
            for i in range(3)
        ]

    offer_price = None
    if screen == "offer":
        offer_price = parse_int(await text("offer-price"), field="offer-price")

    obs = build_observation(
        screen=screen,
        level=parse_int(await text("level-no"), field="level-no"),
        levels_total=parse_int(await text("levels-total"), field="levels-total"),
        gems=parse_int(await text("gems"), field="gems"),
        doors=doors,
        last_result=parse_result(await text("last-result")),
        offer_price=offer_price,
        history=history,
        error=error,
    )
    return validate(obs)


async def read_seq(page) -> int:
    """The render counter. The walker waits for this to rise after every click."""
    return parse_int(await page.locator("#game").get_attribute("data-seq"), field="data-seq")
