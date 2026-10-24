"""Spend accounting and the cap that stops a runaway loop (rule S5).

Cost comes from each response's own usage fields rather than an estimate, so the
number on the dashboard is what was actually billed:

    cost = input*p_in + output*p_out + cache_write*p_cw + cache_read*p_cr

The caps are there to stop a loop, not because the budget is tight: the planned
runs come to roughly $1.30 against a $10 run cap.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any

MILLION = 1_000_000


class BudgetExceeded(RuntimeError):
    """Raised instead of starting work that would cross a cap."""


@dataclass(frozen=True)
class Prices:
    input: float
    output: float
    cache_write: float
    cache_read: float

    @classmethod
    def from_mapping(cls, m: dict[str, float]) -> "Prices":
        return cls(
            input=float(m["input"]),
            output=float(m["output"]),
            cache_write=float(m["cache_write"]),
            cache_read=float(m["cache_read"]),
        )


@dataclass(frozen=True)
class Caps:
    run: float
    development: float
    per_session: float


@dataclass(frozen=True)
class Limits:
    max_steps_per_session: int
    session_timeout_seconds: int
    session_retries: int


@dataclass(frozen=True)
class Pricing:
    model: str
    prices: Prices
    caps: Caps
    limits: Limits
    default_requests_per_minute: int
    header_safety_fraction: float


def load_pricing(path: str | Path = "configs/pricing.yaml") -> Pricing:
    import yaml  # lazy, so the pure helpers here need no dependencies

    data = yaml.safe_load(Path(path).read_text(encoding="utf-8"))
    rl = data.get("rate_limits", {})
    return Pricing(
        model=data["model"],
        prices=Prices.from_mapping(data["prices_usd_per_mtok"]),
        caps=Caps(
            run=float(data["caps_usd"]["run"]),
            development=float(data["caps_usd"]["development"]),
            per_session=float(data["caps_usd"]["per_session"]),
        ),
        limits=Limits(
            max_steps_per_session=int(data["limits"]["max_steps_per_session"]),
            session_timeout_seconds=int(data["limits"]["session_timeout_seconds"]),
            session_retries=int(data["limits"]["session_retries"]),
        ),
        default_requests_per_minute=int(rl.get("default_requests_per_minute", 40)),
        header_safety_fraction=float(rl.get("header_safety_fraction", 0.8)),
    )


@dataclass(frozen=True)
class Usage:
    input_tokens: int = 0
    output_tokens: int = 0
    cache_creation_input_tokens: int = 0
    cache_read_input_tokens: int = 0

    @classmethod
    def from_response(cls, usage: Any) -> "Usage":
        """Read the four counts off an SDK usage object, tolerating absences."""
        get = (usage.get if isinstance(usage, dict) else lambda k, d=0: getattr(usage, k, d) or d)
        return cls(
            input_tokens=int(get("input_tokens", 0) or 0),
            output_tokens=int(get("output_tokens", 0) or 0),
            cache_creation_input_tokens=int(get("cache_creation_input_tokens", 0) or 0),
            cache_read_input_tokens=int(get("cache_read_input_tokens", 0) or 0),
        )

    @property
    def billed_input(self) -> int:
        return self.input_tokens + self.cache_creation_input_tokens + self.cache_read_input_tokens


def cost_usd(usage: Usage, prices: Prices) -> float:
    return (
        usage.input_tokens * prices.input
        + usage.output_tokens * prices.output
        + usage.cache_creation_input_tokens * prices.cache_write
        + usage.cache_read_input_tokens * prices.cache_read
    ) / MILLION


class BudgetGuard:
    """Tracks spend against a cap and refuses to cross it."""

    def __init__(
        self,
        prices: Prices,
        *,
        cap_usd: float,
        per_session_cap_usd: float,
        already_spent_usd: float = 0.0,
        on_spend=None,
    ) -> None:
        self.prices = prices
        self.cap_usd = float(cap_usd)
        self.per_session_cap_usd = float(per_session_cap_usd)
        self._spent = float(already_spent_usd)
        self._per_session: dict[str, float] = {}
        self._on_spend = on_spend

    @property
    def spent_usd(self) -> float:
        return self._spent

    @property
    def remaining_usd(self) -> float:
        return max(0.0, self.cap_usd - self._spent)

    def session_cost_usd(self, session_id: str) -> float:
        return self._per_session.get(session_id, 0.0)

    def check_can_start_session(self) -> None:
        if self._spent >= self.cap_usd:
            raise BudgetExceeded(
                f"cap reached: spent ${self._spent:.4f} of ${self.cap_usd:.2f}"
            )

    def check_estimate(self, estimate_usd: float) -> None:
        """Used by `walker plan`: refuse a batch that cannot fit in what is left."""
        if estimate_usd > self.remaining_usd:
            raise BudgetExceeded(
                f"estimate ${estimate_usd:.4f} exceeds remaining ${self.remaining_usd:.4f} "
                f"of the ${self.cap_usd:.2f} cap"
            )

    def record(self, usage: Usage, *, session_id: str, model: str = "", run_id: str = "") -> float:
        cost = cost_usd(usage, self.prices)
        self._spent += cost
        self._per_session[session_id] = self._per_session.get(session_id, 0.0) + cost
        if self._on_spend is not None:
            self._on_spend({
                "run_id": run_id,
                "session_id": session_id,
                "model": model,
                "input_tokens": usage.input_tokens,
                "output_tokens": usage.output_tokens,
                "cache_creation_input_tokens": usage.cache_creation_input_tokens,
                "cache_read_input_tokens": usage.cache_read_input_tokens,
                "usd": cost,
            })
        return cost

    def session_over_cap(self, session_id: str) -> bool:
        return self.session_cost_usd(session_id) >= self.per_session_cap_usd


# A step is about 1,200 input and 60 output tokens, which already allows for
# Haiku 5.5 counting about 30% more tokens than 4.5 for the same text.
EST_INPUT_TOKENS_PER_STEP = 1_200
EST_OUTPUT_TOKENS_PER_STEP = 60
EST_STEPS_PER_SESSION = 17


def estimate_session_cost(prices: Prices, *, steps: int = EST_STEPS_PER_SESSION) -> float:
    return cost_usd(
        Usage(
            input_tokens=EST_INPUT_TOKENS_PER_STEP * steps,
            output_tokens=EST_OUTPUT_TOKENS_PER_STEP * steps,
        ),
        prices,
    )
