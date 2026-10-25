"""Door Run game server (SPEC section 5).

Player segmentation and the planted switch live here, server-side, like a real
game. The switch is read from the environment once at import time: no HTTP
endpoint can change it, so a running server cannot be talked into a different
condition.

Run with ONE worker, because game state lives in memory:

    PLANTED_SWITCH=on uvicorn game.server:app --host 127.0.0.1 --port 8000 --workers 1
"""

from __future__ import annotations

import json
import os
import secrets
import time
from collections import defaultdict, deque
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Literal

from fastapi import Cookie, FastAPI, HTTPException, Request, Response
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from game import rules as R

STATIC_DIR = Path(__file__).parent / "static"
ORACLE_PATH = Path(os.environ.get("ORACLE_PATH", "oracle/ground_truth.jsonl"))

# Set once, at start. Deliberately not reachable from any route.
_SWITCH_RAW = os.environ.get("PLANTED_SWITCH", "off").strip().lower()
if _SWITCH_RAW not in {"on", "off"}:
    raise RuntimeError(f"PLANTED_SWITCH must be 'on' or 'off', got {_SWITCH_RAW!r}")
SWITCH_ON: bool = _SWITCH_RAW == "on"

# Per-IP rate limit, so the hosted copy survives a crowd scanning the QR code.
RATE_WINDOW_SECONDS = 10.0
RATE_MAX_REQUESTS = 60

app = FastAPI(title="Door Run", docs_url=None, redoc_url=None, openapi_url=None)


@dataclass
class Player:
    pid: str
    seed: int
    level_no: int = 1
    gems: int = R.START_GEMS
    owned: set[str] = field(default_factory=set)
    last_result: str = R.RESULT_NONE
    offer_price: int | None = None
    levels_resolved: int = 0
    done: bool = False

    @property
    def is_spender(self) -> bool:
        return bool(self.owned)

    @property
    def segment(self) -> Literal["spender", "free"]:
        return "spender" if self.is_spender else "free"

    @property
    def targeted(self) -> bool:
        return R.is_targeted(switch_on=SWITCH_ON, is_spender=self.is_spender)


PLAYERS: dict[str, Player] = {}
_RATE: dict[str, deque[float]] = defaultdict(deque)


class BuyBody(BaseModel):
    item_id: str = Field(min_length=1, max_length=40)


class PickBody(BaseModel):
    door: int = Field(ge=0, le=2)


@app.middleware("http")
async def rate_limit(request: Request, call_next):
    ip = request.client.host if request.client else "unknown"
    now = time.monotonic()
    hits = _RATE[ip]
    while hits and now - hits[0] > RATE_WINDOW_SECONDS:
        hits.popleft()
    if len(hits) >= RATE_MAX_REQUESTS:
        return JSONResponse({"detail": "slow down"}, status_code=429)
    hits.append(now)
    return await call_next(request)


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _write_oracle(line: dict) -> None:
    """Ground truth. Off-limits to stats/; only validation/ may read it."""
    ORACLE_PATH.parent.mkdir(parents=True, exist_ok=True)
    with ORACLE_PATH.open("a", encoding="utf-8") as fh:
        fh.write(json.dumps(line, sort_keys=True, separators=(",", ":")) + "\n")


def _player(pid: str | None) -> Player:
    if not pid or pid not in PLAYERS:
        raise HTTPException(status_code=409, detail="no session; call /api/session/start")
    return PLAYERS[pid]


def _level_payload(p: Player) -> dict:
    if p.done:
        return {"level_no": p.level_no, "doors": [], "last_result": p.last_result}
    shown = _shown_for_current_level(p)
    return {
        "level_no": p.level_no,
        "doors": [{"id": i, "win_chance": c} for i, c in enumerate(shown)],
        "last_result": p.last_result,
    }


def _shown_for_current_level(p: Player) -> list[int]:
    base = R.base_chances(p.seed, p.level_no)
    penalty = R.penalty_applied(
        p.seed, p.level_no,
        targeted=p.targeted,
        follows_loss=(p.last_result == R.RESULT_LOSS),
    )
    return R.shown_chances(base, penalty=penalty)


@app.get("/")
async def index(seed: int = 0) -> FileResponse:
    return FileResponse(STATIC_DIR / "index.html")


@app.post("/api/session/start")
async def session_start(response: Response, seed: int = 0) -> dict:
    pid = secrets.token_hex(8)
    PLAYERS[pid] = Player(pid=pid, seed=int(seed))
    response.set_cookie("pid", pid, httponly=True, samesite="strict")
    return {"gems": R.START_GEMS, "levels_total": R.LEVELS_TOTAL}


@app.post("/api/store/buy")
async def store_buy(body: BuyBody, pid: str | None = Cookie(default=None)) -> dict:
    p = _player(pid)
    if p.done:
        raise HTTPException(status_code=409, detail="session over")
    if body.item_id != R.STORE_ITEM_ID:
        raise HTTPException(status_code=404, detail="no such item")
    if body.item_id in p.owned:
        # Buying something already owned changes nothing.
        raise HTTPException(status_code=409, detail="already owned")
    if p.gems < R.STORE_ITEM_PRICE:
        raise HTTPException(status_code=409, detail="not enough gems")

    p.gems -= R.STORE_ITEM_PRICE
    p.owned.add(body.item_id)
    return {"gems": p.gems, "owned": sorted(p.owned)}


@app.get("/api/level/current")
async def level_current(pid: str | None = Cookie(default=None)) -> dict:
    return _level_payload(_player(pid))


@app.post("/api/level/pick")
async def level_pick(body: PickBody, pid: str | None = Cookie(default=None)) -> dict:
    p = _player(pid)
    if p.done:
        raise HTTPException(status_code=409, detail="session over")
    if p.offer_price is not None:
        raise HTTPException(status_code=409, detail="dismiss the offer first")

    level = p.level_no
    base = R.base_chances(p.seed, level)
    penalty = R.penalty_applied(
        p.seed, level, targeted=p.targeted,
        follows_loss=(p.last_result == R.RESULT_LOSS),
    )
    shown = R.shown_chances(base, penalty=penalty)
    won = R.resolve_pick(p.seed, level, shown, body.door)

    offer = None
    if not won and R.offer_shown(p.seed, level, targeted=p.targeted, lost=True):
        offer = R.offer_price(p.seed, level)

    _write_oracle({
        "ts": _now(),
        "seed": p.seed,
        "pid": p.pid,
        "segment": p.segment,
        "switch": "on" if SWITCH_ON else "off",
        "level_no": level,
        "base_chances": base,
        "shown_chances": shown,
        "penalty_applied": penalty,
        "pick": body.door,
        "won": won,
        "offer_shown": offer is not None,
        "offer_prob": R.offer_probability(targeted=p.targeted),
        "offer_price": offer,
    })

    p.last_result = R.RESULT_WIN if won else R.RESULT_LOSS
    p.offer_price = offer
    p.levels_resolved = level
    if level >= R.LEVELS_TOTAL:
        # Stay on the last level; a pending offer must be dismissed before the
        # session can end, so the agent always gets to act on it.
        p.level_no = level
        p.done = offer is None
    else:
        p.level_no = level + 1

    return {
        "result": p.last_result,
        "next_level": None if p.done else p.level_no,
        "offer": None if offer is None else {"price": offer},
        "gems": p.gems,
        "done": p.done,
    }


@app.post("/api/offer/dismiss")
async def offer_dismiss(pid: str | None = Cookie(default=None)) -> dict:
    p = _player(pid)
    if p.offer_price is None:
        raise HTTPException(status_code=409, detail="no offer to dismiss")
    p.offer_price = None
    # Done only once level 10 itself is resolved. level_no has already moved on
    # to the next level, so after an offer on level 9 it reads 10 while level 10
    # is still unplayed; checking it here ended those sessions a level early.
    if p.levels_resolved >= R.LEVELS_TOTAL:
        # The offer was the last thing standing between us and the end.
        p.done = True
    return {"done": p.done, "next_level": None if p.done else p.level_no}


app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")
