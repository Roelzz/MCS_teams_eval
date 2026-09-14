"""Stream loguru output into the UI.

A loguru sink writes formatted lines into a thread-safe queue while a blocking
function runs in a worker thread; the caller drains the queue and pushes lines
into Reflex state. Used by the Setup (provisioning) and Run (eval) pages.
"""

from __future__ import annotations

import asyncio
import queue
from collections.abc import Awaitable, Callable
from contextlib import contextmanager
from typing import Any

from loguru import logger

_FMT = "{time:HH:mm:ss} | {level: <7} | {message}"


@contextmanager
def capture(level: str = "INFO"):
    q: queue.Queue[str] = queue.Queue()
    sink_id = logger.add(lambda m: q.put(str(m).rstrip("\n")), level=level, format=_FMT)
    try:
        yield q
    finally:
        logger.remove(sink_id)


def drain(q: queue.Queue[str]) -> list[str]:
    out: list[str] = []
    try:
        while True:
            out.append(q.get_nowait())
    except queue.Empty:
        pass
    return out


async def run_threaded(
    fn: Callable[..., Any],
    *args: Any,
    on_lines: Callable[[list[str]], Awaitable[None]] | None = None,
    poll: float = 0.3,
) -> tuple[bool, Any, Exception | None]:
    """Run blocking ``fn(*args)`` in a thread, streaming captured log lines to ``on_lines``.

    Returns ``(ok, result, error)``.
    """
    with capture() as q:
        task = asyncio.create_task(asyncio.to_thread(fn, *args))
        while not task.done():
            await asyncio.sleep(poll)
            lines = drain(q)
            if lines and on_lines:
                await on_lines(lines)
        lines = drain(q)
        if lines and on_lines:
            await on_lines(lines)
        try:
            return True, task.result(), None
        except Exception as e:  # noqa: BLE001
            return False, None, e
