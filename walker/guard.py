"""Network guard: the browser may reach exactly two origins (rule S1).

Changing this list is a code change that needs a written decision by both
teammates. It is deliberately not configurable, because a config flag is
something a tired person changes at 1am.

Two independent layers, because one is never enough:
  * request interception aborts anything off the allowlist;
  * Chromium is launched with a host-resolver rule that fails DNS for every
    other host, which also covers the WebSocket traffic that context.route()
    does not intercept. The game opens no sockets, so this is a backstop.
"""

from __future__ import annotations

from urllib.parse import urlsplit  # parsing only; urllib.request is banned by S6

Origin = tuple[str, str, int]


def _normalise(origin: Origin) -> Origin:
    """Scheme and host are case-insensitive; compare them in one case only.

    Without this, an allowlist entry written in capitals never matches what the
    browser reports, and the hosted game is blocked by its own guard -- the kind
    of failure somebody "fixes" at 1am by weakening the allowlist.
    """
    scheme, host, port = origin
    return (scheme.lower(), host.lower(), port)


LOCAL: Origin = _normalise(("http", "127.0.0.1", 8000))
HOSTED: Origin = _normalise(("https", "REPLACE-WITH-OUR-GAME-HOST", 443))
ALLOWED: frozenset[Origin] = frozenset({LOCAL, HOSTED})

# Schemes the browser raises internally that carry no network traffic.
_INERT_SCHEMES = frozenset({"about", "blob", "data"})

DEFAULT_PORTS = {"http": 80, "https": 443}


def origin_of(url: str) -> Origin:
    u = urlsplit(url)
    port = u.port or DEFAULT_PORTS.get((u.scheme or "").lower(), 0)
    return _normalise((u.scheme or "", u.hostname or "", port))


def is_allowed(url: str) -> bool:
    u = urlsplit(url)
    if u.scheme in _INERT_SCHEMES:
        return True
    return origin_of(url) in ALLOWED


def assert_config_origin(origin: str) -> None:
    """Refuse to start a run whose configured origin is not allowlisted (S1)."""
    if origin_of(origin) not in ALLOWED:
        raise ValueError(
            f"origin {origin!r} is not allowlisted. The allowlist lives in "
            "walker/guard.py and changing it requires a written decision by "
            "both teammates."
        )


def allowed_hosts() -> list[str]:
    return sorted({host for _, host, _ in ALLOWED})


def chromium_args() -> list[str]:
    """DNS backstop: resolve nothing except our own hosts."""
    excludes = "".join(f", EXCLUDE {h}" for h in allowed_hosts())
    return [f"--host-resolver-rules=MAP * ~NOTFOUND, EXCLUDE localhost{excludes}"]


# Identical for every persona, so no persona can be told apart by its browser.
CONTEXT_OPTIONS: dict = {
    "service_workers": "block",
    "accept_downloads": False,
    "permissions": [],
    "viewport": {"width": 800, "height": 600},
    "locale": "en-CA",
    "timezone_id": "America/Toronto",
}


async def install_guard(context, log_blocked) -> None:
    """Install request interception. Call before the first navigation."""

    async def handler(route):
        url = route.request.url
        if is_allowed(url):
            await route.continue_()
        else:
            log_blocked(url)
            await route.abort("blockedbyclient")

    await context.route("**/*", handler)
