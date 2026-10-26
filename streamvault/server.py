"""StreamVault server: the audited app (fictional brand, test data only).

The server owns all state and all prices. The page is a thin client that sends
one action at a time to POST /api/act and renders whatever comes back, so the
hidden judge rule never exists in the browser where an agent could read it.

    JUDGE_RULE=fee uvicorn streamvault.server:app --host 127.0.0.1 --port 8000 --workers 1

Several labelled copies can run in one server, each with its own sealed rule,
for the hosted "spot the trap" version (one origin, so the guard is unchanged):

    JUDGE_RULES="a=fee,b=none,c=maze,d=preticked"   -> /a/ /b/ /c/ /d/

Rules are read once at start. No route reads or changes them, and the page
state never contains them. Ground truth
goes to ORACLE_PATH (default oracle/streamvault.jsonl), or to stdout when
ORACLE_SINK=stdout (the hosted container), and only validation/ may read it.
"""

from __future__ import annotations

import json
import os
import secrets
import sys
import time
from collections import defaultdict, deque
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path

from fastapi import FastAPI, HTTPException, Request, Response
from fastapi.responses import FileResponse, JSONResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from streamvault import rules as R

STATIC_DIR = Path(__file__).parent / "static"
ORACLE_PATH = Path(os.environ.get("ORACLE_PATH", "oracle/streamvault.jsonl"))
ORACLE_SINK = os.environ.get("ORACLE_SINK", "file").strip().lower()
RUN_TAG = os.environ.get("RUN_TAG", "")

JUDGE_RULE: str = R.parse_rule(os.environ.get("JUDGE_RULE"))


def parse_copies(raw: str | None) -> dict[str, str]:
    """'a=fee,b=none' -> {'a': 'fee', 'b': 'none'}. Copy names are one lowercase letter."""
    copies: dict[str, str] = {}
    for part in (raw or "").split(","):
        if not part.strip():
            continue
        name, _, rule = part.partition("=")
        name = name.strip().lower()
        if len(name) != 1 or not name.isalpha():
            raise ValueError(f"copy names are single letters, got {name!r}")
        copies[name] = R.parse_rule(rule)
    return copies


# copy name -> rule. "" is the single unlabelled copy served at the root.
COPIES: dict[str, str] = parse_copies(os.environ.get("JUDGE_RULES")) or {"": JUDGE_RULE}

RATE_WINDOW_SECONDS = 10.0
RATE_MAX_REQUESTS = 120
SESSION_TTL_SECONDS = 30 * 60
MAX_SESSIONS = 5_000

app = FastAPI(title="StreamVault", docs_url=None, redoc_url=None, openapi_url=None)

SCREENS = ("landing", "plans", "addons", "checkout", "welcome", "account", "cancel", "cancelled")

# Which actions each screen accepts, and which take an integer argument.
ACTIONS: dict[str, tuple[str, ...]] = {
    "landing": ("rent_movie", "start_signup"),
    "plans": ("pick_plan",),
    "addons": ("toggle_addon", "addons_continue"),
    "checkout": ("confirm_purchase",),
    "welcome": ("go_account",),
    "account": ("cancel_membership",),
    "cancel": ("keep_membership", "continue_cancel"),
    "cancelled": (),
}
ARG_RANGE = {"pick_plan": len(R.PLAN_PRICES), "toggle_addon": len(R.ADDON_PRICES)}


@dataclass
class Member:
    pid: str
    seed: int
    copy: str = ""
    screen: str = "landing"
    rented: bool = False
    treatment: R.Treatment | None = None
    plan: int | None = None
    addons: list[bool] = field(default_factory=lambda: [False] * len(R.ADDON_PRICES))
    cancel_step: int = 0
    continue_clicks: int = 0
    keep_clicks: int = 0
    last_seen: float = field(default_factory=time.monotonic)

    @property
    def segment(self) -> str:
        return "renter" if self.rented else "member"


MEMBERS: dict[str, Member] = {}
_RATE: dict[str, deque[float]] = defaultdict(deque)


class ActBody(BaseModel):
    action: str = Field(min_length=1, max_length=40)
    arg: int | None = Field(default=None, ge=0, le=9)


@app.middleware("http")
async def rate_limit(request: Request, call_next):
    # Behind a TLS proxy, run uvicorn with --proxy-headers so this is the real
    # client address rather than the proxy's.
    ip = request.client.host if request.client else "unknown"
    now = time.monotonic()
    if ip in ("127.0.0.1", "::1"):
        # Local audit batches: every agent shares the loopback address, so a
        # per-IP limit would throttle the audit itself. The limit is for the
        # hosted copy, where it sees each visitor's real address.
        return await call_next(request)
    hits = _RATE[ip]
    while hits and now - hits[0] > RATE_WINDOW_SECONDS:
        hits.popleft()
    if len(hits) >= RATE_MAX_REQUESTS:
        return JSONResponse({"detail": "slow down"}, status_code=429)
    hits.append(now)
    return await call_next(request)


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _oracle(event: str, m: Member) -> None:
    """Ground truth. Off-limits to stats/; only validation/ may read it."""
    t = m.treatment
    line = {
        "ts": _now(), "event": event, "run_tag": RUN_TAG, "pid": m.pid, "seed": m.seed,
        "segment": m.segment, "copy": m.copy, "rule": COPIES[m.copy],
        "rule_applied": bool(t and t.rule_applied),
        "fee": t.fee if t else None, "maze_len": t.maze_len if t else None,
        "preticked": list(t.preticked) if t else None,
        "plan": m.plan, "addons": m.addons,
        "continue_clicks": m.continue_clicks, "keep_clicks": m.keep_clicks,
    }
    text = json.dumps(line, sort_keys=True, separators=(",", ":"))
    if ORACLE_SINK == "stdout":
        print(f"ORACLE {text}", file=sys.stdout, flush=True)
        return
    ORACLE_PATH.parent.mkdir(parents=True, exist_ok=True)
    with ORACLE_PATH.open("a", encoding="utf-8") as fh:
        fh.write(text + "\n")


def _prune() -> None:
    cutoff = time.monotonic() - SESSION_TTL_SECONDS
    for pid in [p for p, m in MEMBERS.items() if m.last_seen < cutoff]:
        del MEMBERS[pid]
    while len(MEMBERS) >= MAX_SESSIONS:
        MEMBERS.pop(next(iter(MEMBERS)))
    for ip in [ip for ip, hits in _RATE.items() if not hits]:
        del _RATE[ip]


def _cookie(copy: str) -> str:
    # One cookie per copy, so a judge can keep several copies open in tabs.
    return f"pid_{copy}" if copy else "pid"


def _member(request: Request, copy: str) -> Member:
    pid = request.cookies.get(_cookie(copy))
    if not pid or pid not in MEMBERS or MEMBERS[pid].copy != copy:
        raise HTTPException(status_code=409, detail="no session; call api/session/start")
    m = MEMBERS[pid]
    m.last_seen = time.monotonic()
    return m


def state(m: Member) -> dict:
    """What the page renders. Never includes the rule or the segment."""
    fee = m.treatment.fee if m.treatment else None
    plan_price = R.PLAN_PRICES[m.plan] if m.plan is not None else None
    addons_total = sum(p for p, on in zip(R.ADDON_PRICES, m.addons) if on)
    checkout = None
    if plan_price is not None and fee is not None:
        checkout = {
            "plan": plan_price, "addons": addons_total, "fees": fee,
            "total": R.checkout_total(plan_price, m.addons, fee),
        }
    return {
        "screen": m.screen,
        "rented": m.rented,
        "headline_price": R.HEADLINE_PRICE,
        "rent_price": R.RENT_PRICE,
        "plan_prices": list(R.PLAN_PRICES),
        "addons": [{"id": i, "price": p, "checked": m.addons[i]}
                   for i, p in enumerate(R.ADDON_PRICES)],
        "checkout": checkout,
        "cancel_step": m.cancel_step,
        "cancel_message": R.CANCEL_MESSAGES[(m.cancel_step - 1) % len(R.CANCEL_MESSAGES)]
        if m.cancel_step else "",
        "allowed": [a for a in ACTIONS[m.screen] if not (a == "rent_movie" and m.rented)],
    }


@app.get("/healthz")
async def healthz() -> dict:
    return {"ok": True}


def _apply(m: Member, a: str, arg: int | None) -> None:
    rule = COPIES[m.copy]
    if a == "rent_movie":
        m.rented = True
    elif a == "start_signup":
        # The segment is fixed here, from what the member did before signing up.
        m.treatment = R.treatment(m.seed, rule=rule, renter=m.rented)
        m.screen = "plans"
    elif a == "pick_plan":
        m.plan = arg
        m.addons = list(m.treatment.preticked) if m.treatment else [False] * 4
        m.screen = "addons"
    elif a == "toggle_addon":
        m.addons[arg] = not m.addons[arg]
    elif a == "addons_continue":
        m.screen = "checkout"
    elif a == "confirm_purchase":
        _oracle("checkout", m)
        m.screen = "welcome"
    elif a == "go_account":
        m.screen = "account"
    elif a == "cancel_membership":
        m.cancel_step = 1
        m.screen = "cancel"
    elif a == "keep_membership":
        m.keep_clicks += 1
        m.cancel_step = 0
        m.screen = "account"
    elif a == "continue_cancel":
        m.continue_clicks += 1
        assert m.treatment is not None
        if m.cancel_step >= m.treatment.maze_len:
            m.cancel_step = 0
            m.screen = "cancelled"
            _oracle("cancelled", m)
        else:
            m.cancel_step += 1


def _register(copy: str) -> None:
    """Routes for one copy, under /<copy>/ (or the root for the unlabelled copy)."""
    prefix = f"/{copy}" if copy else ""

    async def index() -> FileResponse:
        return FileResponse(STATIC_DIR / "index.html")

    async def session_start(request: Request, response: Response, seed: int = 0) -> dict:
        _prune()
        pid = secrets.token_hex(8)
        m = Member(pid=pid, seed=int(seed), copy=copy)
        MEMBERS[pid] = m
        secure = request.headers.get("x-forwarded-proto", request.url.scheme) == "https"
        # root_path is set when this app is mounted under the hub (e.g. "/streamvault").
        root = request.scope.get("root_path", "")
        response.set_cookie(_cookie(copy), pid, httponly=True, samesite="strict", secure=secure,
                            path=f"{root}{prefix}/")
        return state(m)

    async def get_state(request: Request) -> dict:
        return state(_member(request, copy))

    async def act(body: ActBody, request: Request) -> dict:
        m = _member(request, copy)
        a = body.action
        if a not in state(m)["allowed"]:
            raise HTTPException(status_code=409, detail=f"{a} is not available on {m.screen}")
        if a in ARG_RANGE and (body.arg is None or not 0 <= body.arg < ARG_RANGE[a]):
            raise HTTPException(status_code=422, detail=f"{a} needs an argument 0..{ARG_RANGE[a] - 1}")
        _apply(m, a, body.arg)
        return state(m)

    app.add_api_route(f"{prefix}/", index, methods=["GET"])
    app.add_api_route(f"{prefix}/api/session/start", session_start, methods=["POST"])
    app.add_api_route(f"{prefix}/api/state", get_state, methods=["GET"])
    app.add_api_route(f"{prefix}/api/act", act, methods=["POST"])
    if prefix:
        async def to_slash(request: Request) -> RedirectResponse:
            return RedirectResponse(f"{request.scope.get('root_path', '')}{prefix}/")
        app.add_api_route(prefix, to_slash, methods=["GET"])


for _copy in COPIES:
    _register(_copy)

if "" not in COPIES:
    from fastapi.responses import HTMLResponse

    DASHBOARD_URL = os.environ.get("DASHBOARD_URL", "#")

    @app.get("/", response_class=HTMLResponse)
    async def chooser() -> str:
        """The 'spot the trap' landing page linking to every copy (never the rules)."""
        links = "\n    ".join(
            f'<a class="copy" href="{c}/"><div class="letter">{c.upper()}</div>'
            f'<div class="small">StreamVault copy {c.upper()}</div></a>'
            for c in sorted(COPIES))
        html = (STATIC_DIR / "chooser.html").read_text(encoding="utf-8")
        # A same-site path ("/dashboard.html") is fine; "//host" would be another site.
        same_site = DASHBOARD_URL.startswith("/") and not DASHBOARD_URL.startswith("//")
        safe_url = DASHBOARD_URL if same_site or DASHBOARD_URL.startswith(("https://", "http://", "#")) else "#"
        return html.replace("{{COPY_LINKS}}", links).replace("{{DASHBOARD_URL}}", safe_url)


app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")
