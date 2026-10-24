"""The rate limiter: pace to 80% of the reported limits, honour retry-after.

Time and sleep are injected, so these run instantly and deterministically
instead of actually waiting a minute.
"""

from __future__ import annotations

import pytest

from walker.ratelimit import (
    HEADER_INPUT_TOKENS_LIMIT, HEADER_REQUESTS_LIMIT, HEADER_RETRY_AFTER, RateLimiter,
)


class Clock:
    def __init__(self) -> None:
        self.now = 1000.0
        self.slept: list[float] = []

    def __call__(self) -> float:
        return self.now

    async def sleep(self, seconds: float) -> None:
        self.slept.append(seconds)
        self.now += seconds


def limiter(**kwargs) -> tuple[RateLimiter, Clock]:
    clock = Clock()
    return RateLimiter(clock=clock, sleep=clock.sleep, **kwargs), clock


def test_defaults_to_forty_requests_per_minute_until_headers_arrive() -> None:
    rl, _ = limiter()
    assert rl.requests_per_minute == 32  # 80% of 40
    assert rl.input_tokens_per_minute is None
    assert not rl.saw_headers


def test_adopts_the_reported_limits_at_eighty_percent() -> None:
    rl, _ = limiter()
    rl.update_from_headers({HEADER_REQUESTS_LIMIT: "1000",
                            HEADER_INPUT_TOKENS_LIMIT: "200000"})
    assert rl.requests_per_minute == 800
    assert rl.input_tokens_per_minute == 160_000
    assert rl.saw_headers


def test_a_missing_header_leaves_the_default_in_force() -> None:
    rl, _ = limiter()
    rl.update_from_headers({})
    assert rl.requests_per_minute == 32
    assert not rl.saw_headers

    rl.update_from_headers(None)
    assert rl.requests_per_minute == 32


def test_a_garbled_header_is_ignored_rather_than_crashing() -> None:
    rl, _ = limiter()
    rl.update_from_headers({HEADER_REQUESTS_LIMIT: "lots"})
    assert rl.requests_per_minute == 32
    assert not rl.saw_headers


@pytest.mark.asyncio
async def test_requests_are_paced_once_the_window_is_full() -> None:
    rl, clock = limiter(default_requests_per_minute=10)  # 80% -> 8 per minute
    for _ in range(8):
        await rl.acquire()
    assert clock.slept == [], "the first eight should go straight through"

    await rl.acquire()
    assert clock.slept, "the ninth must wait for the window to roll"
    assert clock.slept[0] == pytest.approx(60.0, abs=0.01)


@pytest.mark.asyncio
async def test_input_tokens_are_paced_too() -> None:
    rl, clock = limiter(default_requests_per_minute=10_000)
    rl.update_from_headers({HEADER_INPUT_TOKENS_LIMIT: "10000"})  # 80% -> 8000
    await rl.acquire(5_000)
    assert clock.slept == []
    await rl.acquire(5_000)  # would take us to 10,000 > 8,000
    assert clock.slept
    assert clock.slept[0] == pytest.approx(60.0, abs=0.01)


@pytest.mark.asyncio
async def test_retry_after_parks_every_caller() -> None:
    rl, clock = limiter()
    waited = rl.note_retry_after({HEADER_RETRY_AFTER: "30"})
    assert waited == 30.0
    await rl.acquire()
    assert clock.slept[0] == pytest.approx(30.0, abs=0.01)


@pytest.mark.asyncio
async def test_a_missing_retry_after_falls_back_to_a_short_pause() -> None:
    rl, _ = limiter()
    assert rl.note_retry_after({}) == 1.0
    assert rl.note_retry_after({HEADER_RETRY_AFTER: "nonsense"}) == 1.0


def test_retry_after_is_clamped_so_a_bad_header_cannot_stall_the_batch() -> None:
    rl, _ = limiter()
    assert rl.note_retry_after({HEADER_RETRY_AFTER: "999999"}) == 300.0
    assert rl.note_retry_after({HEADER_RETRY_AFTER: "-5"}) == 0.0


@pytest.mark.asyncio
async def test_the_window_slides_rather_than_resetting() -> None:
    rl, clock = limiter(default_requests_per_minute=10)  # 8 per minute
    for _ in range(8):
        await rl.acquire()
    clock.now += 61.0  # the whole window has aged out
    await rl.acquire()
    assert clock.slept == [], "after a minute the budget should be free again"
