"""Rate limiter (rule S5).

Rate limits, not money, are what actually sets how long a batch takes. The
limiter paces requests and input tokens to 80% of whatever the API's
rate-limit headers report, falling back to 40 requests/min until the first
response arrives and telling us what the real limits are.

Sliding 60-second windows, because the API's limits are per minute.
"""

from __future__ import annotations

import asyncio
import time
from collections import deque

WINDOW_SECONDS = 60.0

HEADER_REQUESTS_LIMIT = "anthropic-ratelimit-requests-limit"
HEADER_INPUT_TOKENS_LIMIT = "anthropic-ratelimit-input-tokens-limit"
HEADER_RETRY_AFTER = "retry-after"


class RateLimiter:
    def __init__(
        self,
        *,
        default_requests_per_minute: int = 40,
        fraction: float = 0.8,
        clock=time.monotonic,
        sleep=asyncio.sleep,
    ) -> None:
        self._fraction = fraction
        self._clock = clock
        self._sleep = sleep
        self._requests_limit = default_requests_per_minute
        self._input_tokens_limit: int | None = None
        self._saw_headers = False
        self._requests: deque[float] = deque()
        self._tokens: deque[tuple[float, int]] = deque()
        self._lock = asyncio.Lock()
        self._blocked_until = 0.0

    @property
    def requests_per_minute(self) -> int:
        return max(1, int(self._requests_limit * self._fraction))

    @property
    def input_tokens_per_minute(self) -> int | None:
        if self._input_tokens_limit is None:
            return None
        return max(1, int(self._input_tokens_limit * self._fraction))

    @property
    def saw_headers(self) -> bool:
        return self._saw_headers

    def update_from_headers(self, headers) -> None:
        """Adopt the real limits. A missing header leaves the default in place."""
        def read(name: str) -> int | None:
            raw = headers.get(name) if headers is not None else None
            if raw is None:
                return None
            try:
                return int(str(raw).strip())
            except ValueError:
                return None

        requests = read(HEADER_REQUESTS_LIMIT)
        tokens = read(HEADER_INPUT_TOKENS_LIMIT)
        if requests is not None:
            self._requests_limit = requests
            self._saw_headers = True
        if tokens is not None:
            self._input_tokens_limit = tokens
            self._saw_headers = True

    def note_retry_after(self, headers) -> float:
        """Honour retry-after on a 429 by parking every caller until it passes."""
        raw = headers.get(HEADER_RETRY_AFTER) if headers is not None else None
        try:
            seconds = float(str(raw).strip())
        except (TypeError, ValueError):
            seconds = 1.0
        seconds = max(0.0, min(seconds, 300.0))
        self._blocked_until = max(self._blocked_until, self._clock() + seconds)
        return seconds

    def _prune(self, now: float) -> None:
        while self._requests and now - self._requests[0] > WINDOW_SECONDS:
            self._requests.popleft()
        while self._tokens and now - self._tokens[0][0] > WINDOW_SECONDS:
            self._tokens.popleft()

    def _wait_for(self, now: float, estimated_input_tokens: int) -> float:
        self._prune(now)
        waits = [max(0.0, self._blocked_until - now)]

        if len(self._requests) >= self.requests_per_minute:
            waits.append(WINDOW_SECONDS - (now - self._requests[0]))

        token_limit = self.input_tokens_per_minute
        if token_limit is not None and self._tokens:
            used = sum(n for _, n in self._tokens)
            if used + estimated_input_tokens > token_limit:
                waits.append(WINDOW_SECONDS - (now - self._tokens[0][0]))

        return max(waits)

    async def acquire(self, estimated_input_tokens: int = 0) -> None:
        """Block until a request may be sent, then record it against the window."""
        async with self._lock:
            while True:
                now = self._clock()
                wait = self._wait_for(now, estimated_input_tokens)
                if wait <= 0:
                    break
                await self._sleep(wait)
            now = self._clock()
            self._requests.append(now)
            if estimated_input_tokens:
                self._tokens.append((now, estimated_input_tokens))
