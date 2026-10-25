"""A fake page that plays the real game rules, with no browser.

This mirrors game/server.py's state machine and reads/writes the same DOM
contract, which lets the agent loop be tested end to end at zero cost. The
contract is the narrow part of the system, so a fake that honours it exercises
nearly everything the real browser path does.
"""

from __future__ import annotations

from pathlib import Path

from game import rules as R


class FakeGame:
    def __init__(self, seed: int = 1, *, switch_on: bool = False) -> None:
        self.seed = seed
        self.switch_on = switch_on
        self.screen = "start"
        self.level_no = 1
        self.gems = R.START_GEMS
        self.owned: set[str] = set()
        self.last_result = R.RESULT_NONE
        self.offer_price: int | None = None
        self.levels_resolved = 0
        self.done = False
        self.seq = 0
        self.oracle: list[dict] = []

    @property
    def is_spender(self) -> bool:
        return bool(self.owned)

    @property
    def targeted(self) -> bool:
        return R.is_targeted(switch_on=self.switch_on, is_spender=self.is_spender)

    def shown_chances(self) -> list[int]:
        base = R.base_chances(self.seed, self.level_no)
        penalty = R.penalty_applied(
            self.seed, self.level_no, targeted=self.targeted,
            follows_loss=(self.last_result == R.RESULT_LOSS),
        )
        return R.shown_chances(base, penalty=penalty)

    def click(self, test_id: str) -> None:
        if test_id == "open-store":
            self.screen = "store"
        elif test_id == "close-store":
            self.screen = "level"
        elif test_id == "buy-gold-frame":
            if R.STORE_ITEM_ID not in self.owned and self.gems >= R.STORE_ITEM_PRICE:
                self.gems -= R.STORE_ITEM_PRICE
                self.owned.add(R.STORE_ITEM_ID)
        elif test_id == "offer-dismiss":
            self.offer_price = None
            if self.levels_resolved >= R.LEVELS_TOTAL:
                self.done = True
                self.screen = "done"
            else:
                self.screen = "level"
        elif test_id.startswith("door-"):
            self._pick(int(test_id.split("-")[1]))
        else:
            raise AssertionError(f"clicked an element that is not in the contract: {test_id}")
        self.seq += 1

    def _pick(self, door: int) -> None:
        level = self.level_no
        shown = self.shown_chances()
        won = R.resolve_pick(self.seed, level, shown, door)
        offer = None
        if not won and R.offer_shown(self.seed, level, targeted=self.targeted, lost=True):
            offer = R.offer_price(self.seed, level)

        self.oracle.append({
            "level_no": level, "segment": "spender" if self.is_spender else "free",
            "switch": "on" if self.switch_on else "off", "shown_chances": shown,
            "pick": door, "won": won, "offer_shown": offer is not None,
            "offer_price": offer, "seed": self.seed, "pid": "fake",
            "offer_prob": R.offer_probability(targeted=self.targeted),
        })

        self.last_result = R.RESULT_WIN if won else R.RESULT_LOSS
        self.offer_price = offer
        self.levels_resolved = level
        if level >= R.LEVELS_TOTAL:
            self.level_no = level
            self.done = offer is None
        else:
            self.level_no = level + 1

        if offer is not None:
            self.screen = "offer"
        elif self.done:
            self.screen = "done"
        else:
            self.screen = "level"

    # --- the DOM contract -----------------------------------------------------

    def text(self, test_id: str) -> str | None:
        shown = self.shown_chances() if self.screen == "level" else [0, 0, 0]
        values = {
            "level-no": str(self.level_no),
            "levels-total": str(R.LEVELS_TOTAL),
            "gems": str(self.gems),
            "last-result": self.last_result,
            "door-0-chance": str(shown[0]),
            "door-1-chance": str(shown[1]),
            "door-2-chance": str(shown[2]),
            "offer-price": str(self.offer_price) if self.offer_price is not None else None,
        }
        return values.get(test_id)


class _Locator:
    def __init__(self, page: "FakePage", test_id: str) -> None:
        self._page = page
        self._test_id = test_id

    async def count(self) -> int:
        return 1 if self._page.game.text(self._test_id) is not None else 0

    @property
    def first(self) -> "_Locator":
        return self

    async def text_content(self) -> str | None:
        override = self._page.injected.get(self._test_id)
        if override is not None:
            return override
        return self._page.game.text(self._test_id)

    async def click(self) -> None:
        self._page.clicks.append(self._test_id)
        self._page.game.click(self._test_id)


class _RootLocator:
    def __init__(self, page: "FakePage") -> None:
        self._page = page

    async def get_attribute(self, name: str) -> str | None:
        if name == "data-state":
            return self._page.injected.get("#game[data-state]", self._page.game.screen)
        if name == "data-seq":
            return str(self._page.game.seq)
        return None


class FakePage:
    """Exposes only what observe.py and agent.py actually use.

    `injected` lets a test put hostile content into a label, standing in for a
    compromised page, to prove it never reaches the model.
    """

    def __init__(self, game: FakeGame, injected: dict[str, str] | None = None) -> None:
        self.game = game
        self.injected = injected or {}
        self.clicks: list[str] = []
        self.screenshots = 0

    def locator(self, selector: str) -> _RootLocator:
        assert selector == "#game", f"unexpected selector {selector!r}"
        return _RootLocator(self)

    def get_by_test_id(self, test_id: str) -> _Locator:
        return _Locator(self, test_id)

    async def screenshot(self, path: str | None = None) -> bytes:
        """Mirrors Playwright: writes the file when given a path, and returns the bytes.

        Writing it matters -- the integrity check rehashes the PNG on disk, so a
        fake that only returned bytes would make verification fail for a reason
        the real browser never produces.
        """
        self.screenshots += 1
        png = b"\x89PNG\r\n\x1a\n fake screenshot bytes " + str(self.screenshots).encode()
        if path is not None:
            target = Path(path)
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(png)
        return png


class RecordingLLM:
    """Wraps an LLM and records every observation it was asked about."""

    def __init__(self, inner) -> None:
        self.inner = inner
        self.seen: list[dict] = []
        self.prompts: list[str] = []

    async def decide(self, system_prompt: str, observation: dict):
        self.seen.append(observation)
        self.prompts.append(system_prompt)
        return await self.inner.decide(system_prompt, observation)


class ScriptedLLM:
    """Returns a fixed list of decisions.

    Once the script runs out it falls back to `default` if one was given, and
    otherwise raises -- so a test that meant to script the whole session fails
    loudly rather than quietly drifting.
    """

    def __init__(self, decisions: list, *, default=None) -> None:
        self.decisions = list(decisions)
        self.default = default
        self.calls = 0

    async def decide(self, system_prompt: str, observation: dict):
        self.calls += 1
        if self.decisions:
            return self.decisions.pop(0)
        if self.default is not None:
            return self.default(observation) if callable(self.default) else self.default
        raise AssertionError("ScriptedLLM ran out of decisions")


def collect_events() -> tuple[list[tuple], object]:
    """An `emit` that appends to a list, for SessionDeps."""
    events: list[tuple] = []

    async def emit(type: str, payload: dict, *, level_no=None, screenshot_id=None) -> None:
        events.append((type, payload, level_no, screenshot_id))

    return events, emit


async def null_capture(label: str) -> None:
    return None
