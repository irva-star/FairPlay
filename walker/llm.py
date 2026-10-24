"""The model call. This is the ONLY file that imports `anthropic` (rule S6).

Every constraint below comes from the Haiku 5.5 migration guide:
  * never send temperature, top_p or top_k: Haiku 5.5 does not accept them;
  * never end messages with an assistant turn: it rejects prefill;
  * never set thinking: with a forced tool_choice it opens with the tool call;
  * select content blocks by type, never by position.

A reply that carries no usable tool call -- refusal, max_tokens, or simply no
tool_use block -- is reported as an invalid call rather than guessed at. The
agent loop gives it one retry and then ends the session as a protocol
deviation, because an audit that quietly improvises is not an audit.
"""

from __future__ import annotations

import json
import random
import time
from dataclasses import dataclass, field
from typing import Any, Protocol

from walker.budget import Usage
from walker.prompts import TOOLS

MODEL = "claude-haiku-5-5"
MAX_TOKENS = 256


@dataclass(frozen=True)
class Decision:
    tool: str | None
    args: dict[str, Any] = field(default_factory=dict)
    usage: Usage = field(default_factory=Usage)
    stop_reason: str = ""
    model: str = ""
    latency_ms: int = 0
    invalid_reason: str | None = None

    @property
    def is_valid(self) -> bool:
        return self.tool is not None and self.invalid_reason is None


class LLMClient(Protocol):
    async def decide(self, system_prompt: str, observation: dict) -> Decision: ...


def _first_tool_use(content) -> Any | None:
    for block in content or []:
        if getattr(block, "type", None) == "tool_use":
            return block
    return None


class AnthropicWalker:
    """Wraps the SDK. The rate limiter, not this class, decides when to send."""

    def __init__(self, limiter, *, model: str = MODEL, api_key: str | None = None) -> None:
        from anthropic import AsyncAnthropic

        # max_retries handles rate-limit and server errors with backoff.
        self._client = AsyncAnthropic(max_retries=3, **({"api_key": api_key} if api_key else {}))
        self._limiter = limiter
        self.model = model

    async def decide(self, system_prompt: str, observation: dict) -> Decision:
        from anthropic import APIStatusError, RateLimitError

        payload = json.dumps(observation, sort_keys=True, separators=(",", ":"))
        await self._limiter.acquire(estimated_input_tokens=1_200)

        started = time.monotonic()
        try:
            raw = await self._client.messages.with_raw_response.create(
                model=self.model,
                max_tokens=MAX_TOKENS,
                system=[{
                    "type": "text",
                    "text": system_prompt,
                    "cache_control": {"type": "ephemeral"},
                }],
                tools=TOOLS,
                tool_choice={"type": "any", "disable_parallel_tool_use": True},
                messages=[{"role": "user", "content": payload}],
            )
        except RateLimitError as exc:
            self._limiter.note_retry_after(getattr(exc.response, "headers", None))
            raise
        except APIStatusError as exc:
            self._limiter.update_from_headers(getattr(exc.response, "headers", None))
            raise

        latency_ms = int((time.monotonic() - started) * 1000)
        self._limiter.update_from_headers(raw.headers)
        message = raw.parse()

        usage = Usage.from_response(getattr(message, "usage", None))
        stop_reason = getattr(message, "stop_reason", "") or ""
        common = {"usage": usage, "stop_reason": stop_reason,
                  "model": getattr(message, "model", self.model), "latency_ms": latency_ms}

        if stop_reason == "refusal":
            return Decision(tool=None, invalid_reason="refusal", **common)
        if stop_reason == "max_tokens":
            return Decision(tool=None, invalid_reason="max_tokens", **common)

        block = _first_tool_use(getattr(message, "content", None))
        if block is None:
            return Decision(tool=None, invalid_reason="no_tool_use", **common)

        args = dict(getattr(block, "input", None) or {})
        return Decision(tool=getattr(block, "name", None), args=args, **common)


class FakeLLM:
    """Zero-cost stub for development (`--llm fake`). Never used for a measured run."""

    def __init__(self, seed: int = 0) -> None:
        self._rng = random.Random(seed)

    async def decide(self, system_prompt: str, observation: dict) -> Decision:
        allowed = list(observation.get("allowed_actions") or [])
        if not allowed:
            return Decision(tool=None, invalid_reason="no_tool_use", stop_reason="end_turn",
                            model="fake", latency_ms=0)
        tool = self._rng.choice(allowed)
        args: dict[str, Any] = {"reason": "fake stub"}
        if tool == "pick_door":
            doors = observation.get("doors") or []
            best = max(doors, key=lambda d: d["win_chance"]) if doors else {"id": 0}
            args["door"] = int(best["id"])
        elif tool == "buy_item":
            args["item_id"] = "gold_frame"
        return Decision(tool=tool, args=args, usage=Usage(), stop_reason="tool_use",
                        model="fake", latency_ms=0)
