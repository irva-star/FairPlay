"""The single evidence writer (SPEC section 7).

During a run exactly one task writes to SQLite, fed by an asyncio.Queue. With
WAL mode that lets the local dashboard read the live run while sessions are
still playing, and it means the hash chains need no locking: each session's
events arrive in order from one coroutine.

Screenshots are hashed from the PNG bytes immediately after capture, before
anything else can touch the file.
"""

from __future__ import annotations

import asyncio
import hashlib
from dataclasses import dataclass
from pathlib import Path
from typing import Any

_STOP = object()


@dataclass
class _Event:
    session_id: str
    type: str
    payload: dict[str, Any]
    level_no: int | None = None
    screenshot_id: str | None = None


class EvidenceWriter:
    def __init__(self, store, *, screenshots_dir: str | Path = "data/screenshots") -> None:
        self.store = store
        self.dir = Path(screenshots_dir)
        self.dir.mkdir(parents=True, exist_ok=True)
        self.queue: asyncio.Queue = asyncio.Queue()
        self._task: asyncio.Task | None = None
        self._counter = 0

    async def __aenter__(self) -> "EvidenceWriter":
        self._task = asyncio.create_task(self._drain(), name="evidence-writer")
        return self

    async def __aexit__(self, *exc) -> None:
        await self.close()

    async def close(self) -> None:
        if self._task is None:
            return
        await self.queue.put(_STOP)
        await self._task
        self._task = None

    async def _drain(self) -> None:
        while True:
            item = await self.queue.get()
            try:
                if item is _STOP:
                    return
                self.store.append_event(
                    item.session_id, type=item.type, payload=item.payload,
                    level_no=item.level_no, screenshot_id=item.screenshot_id,
                )
            finally:
                self.queue.task_done()

    def emitter(self, session_id: str):
        """An `emit` callable bound to one session, for SessionDeps."""

        async def emit(type: str, payload: dict[str, Any], *, level_no: int | None = None,
                       screenshot_id: str | None = None) -> None:
            await self.queue.put(_Event(session_id, type, payload, level_no, screenshot_id))

        return emit

    def capturer(self, page, session_id: str):
        """A `capture` callable that screenshots, hashes and records the PNG."""

        async def capture(label: str) -> str | None:
            self._counter += 1
            screenshot_id = f"{session_id}-{self._counter:03d}-{label}"
            path = self.dir / f"{screenshot_id}.png"
            png = await page.screenshot(path=str(path))
            digest = hashlib.sha256(png).hexdigest() if png else _sha256_file(path)
            self.store.add_screenshot(screenshot_id, path=path, sha256=digest)
            await self.queue.put(_Event(session_id, "screenshot", {"label": label},
                                        screenshot_id=screenshot_id))
            return screenshot_id

        return capture


def _sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as fh:
        while block := fh.read(65536):
            h.update(block)
    return h.hexdigest()


class NullCapture:
    """Used when screenshots are off (fake-LLM smoke runs)."""

    async def __call__(self, label: str) -> None:
        return None
