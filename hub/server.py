"""The hosted site: landing page, dashboard and both audited apps on one origin.

    uvicorn hub.server:app --host 127.0.0.1 --port 8000 --workers 1

One origin matters because both apps keep their session in a SameSite=Strict
cookie. Embedded from another *.onrender.com service, those cookies would never
be sent and the games would break inside the landing page's frame.

The apps are mounted unchanged; each still reads its own hidden rule from the
environment (PLANTED_SWITCH, JUDGE_RULES). Local audit batches keep running the
apps directly, never through the hub. Nothing here imports walker or anthropic.
"""

from __future__ import annotations

from pathlib import Path

from fastapi import FastAPI
from fastapi.responses import RedirectResponse
from fastapi.staticfiles import StaticFiles

from game.server import app as doorrun
from streamvault.server import app as streamvault

STATIC_DIR = Path(__file__).resolve().parent / "static"

app = FastAPI(title="FairPlay Labs", docs_url=None, redoc_url=None, openapi_url=None)


@app.get("/healthz")
async def healthz() -> dict:
    return {"ok": True}


# Both apps resolve their paths relative to the page, so they need the slash.
@app.get("/doorrun", include_in_schema=False)
async def doorrun_slash() -> RedirectResponse:
    return RedirectResponse("/doorrun/")


@app.get("/streamvault", include_in_schema=False)
async def streamvault_slash() -> RedirectResponse:
    return RedirectResponse("/streamvault/")


app.mount("/doorrun", doorrun)
app.mount("/streamvault", streamvault)
app.mount("/", StaticFiles(directory=STATIC_DIR, html=True), name="site")
