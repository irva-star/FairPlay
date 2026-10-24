"""Rule S1: the browser reaches only our two game origins."""

from __future__ import annotations

import pytest

from walker import guard


@pytest.mark.parametrize("url", [
    "http://127.0.0.1:8000/",
    "http://127.0.0.1:8000/static/game.js",
    "http://127.0.0.1:8000/api/level/pick?x=1",
    f"https://{guard.HOSTED[1]}/",
    f"https://{guard.HOSTED[1]}/static/style.css",
])
def test_allows_our_origins(url: str) -> None:
    assert guard.is_allowed(url)


@pytest.mark.parametrize("url", [
    "https://example.com/",
    "http://example.com/",
    "https://api.anthropic.com/v1/messages",
    "http://127.0.0.1:8001/",          # right host, wrong port
    "https://127.0.0.1:8000/",         # right host and port, wrong scheme
    "http://127.0.0.2:8000/",          # neighbouring address
    "http://localhost:8000/",          # not the same origin as 127.0.0.1
    "file:///etc/passwd",
    "ftp://127.0.0.1:8000/",
    "http://127.0.0.1:8000@evil.com/",  # userinfo trick: host is evil.com
    "http://evil.com/?x=http://127.0.0.1:8000",
])
def test_blocks_everything_else(url: str) -> None:
    assert not guard.is_allowed(url)


@pytest.mark.parametrize("url", [
    f"https://{guard.HOSTED[1].upper()}/",
    "HTTP://127.0.0.1:8000/",
])
def test_host_and_scheme_comparison_is_case_insensitive(url: str) -> None:
    """A capitalised allowlist entry must still match what the browser reports,
    otherwise the hosted game is blocked by its own guard."""
    assert guard.is_allowed(url)


def test_inert_schemes_are_not_network_traffic() -> None:
    for url in ("about:blank", "data:text/html,<p>x", "blob:something"):
        assert guard.is_allowed(url)


def test_public_url_in_config_refuses_to_start() -> None:
    with pytest.raises(ValueError, match="not allowlisted"):
        guard.assert_config_origin("https://example.com")
    with pytest.raises(ValueError):
        guard.assert_config_origin("http://127.0.0.1:9999")


def test_allowlisted_origins_pass_the_config_check() -> None:
    guard.assert_config_origin("http://127.0.0.1:8000")
    guard.assert_config_origin(f"https://{guard.HOSTED[1]}")


def test_dns_backstop_excludes_only_our_hosts() -> None:
    args = guard.chromium_args()
    rule = next(a for a in args if a.startswith("--host-resolver-rules="))
    assert "MAP * ~NOTFOUND" in rule
    for host in guard.allowed_hosts():
        assert f"EXCLUDE {host}" in rule
    assert "EXCLUDE example.com" not in rule


def test_context_options_are_locked_down() -> None:
    options = guard.CONTEXT_OPTIONS
    assert options["service_workers"] == "block"
    assert options["accept_downloads"] is False
    assert options["permissions"] == []


class FakeRequest:
    def __init__(self, url: str) -> None:
        self.url = url


class FakeRoute:
    def __init__(self, url: str) -> None:
        self.request = FakeRequest(url)
        self.continued = False
        self.aborted_with: str | None = None

    async def continue_(self) -> None:
        self.continued = True

    async def abort(self, reason: str) -> None:
        self.aborted_with = reason


class FakeContext:
    def __init__(self) -> None:
        self.handler = None
        self.pattern = None

    async def route(self, pattern, handler) -> None:
        self.pattern = pattern
        self.handler = handler


@pytest.mark.asyncio
async def test_handler_aborts_offsite_requests_and_logs_them() -> None:
    context = FakeContext()
    blocked: list[str] = []
    await guard.install_guard(context, blocked.append)
    assert context.pattern == "**/*"

    offsite = FakeRoute("https://example.com/beacon")
    await context.handler(offsite)
    assert offsite.aborted_with == "blockedbyclient"
    assert not offsite.continued
    assert blocked == ["https://example.com/beacon"]

    ours = FakeRoute("http://127.0.0.1:8000/api/level/current")
    await context.handler(ours)
    assert ours.continued
    assert ours.aborted_with is None
    assert blocked == ["https://example.com/beacon"]  # unchanged
