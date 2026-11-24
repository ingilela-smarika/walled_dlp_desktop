"""Minimal asyncio Chrome DevTools Protocol client (flat session mode)."""
from __future__ import annotations

import asyncio
import json

import websockets


class CDPError(Exception):
    pass


class CDPConnection:
    def __init__(self) -> None:
        self._ws = None
        self._next = 0
        self._pending: dict[int, asyncio.Future] = {}
        self._listeners: list = []
        self.closed = asyncio.Event()
        self._wlock = asyncio.Lock()   # FIFO: commands reach the browser in the order they were issued

    async def connect(self, url: str) -> None:
        self._ws = await websockets.connect(url, max_size=None, ping_interval=None)
        asyncio.create_task(self._reader())

    def on_event(self, fn) -> None:
        self._listeners.append(fn)

    async def send(self, method: str, params: dict | None = None, session_id: str | None = None,
                   timeout: float = 30) -> dict:
        self._next += 1
        msg = {"id": self._next, "method": method, "params": params or {}}
        if session_id:
            msg["sessionId"] = session_id
        fut = asyncio.get_running_loop().create_future()
        self._pending[self._next] = fut
        async with self._wlock:
            await self._ws.send(json.dumps(msg))
        return await asyncio.wait_for(fut, timeout)

    async def _reader(self) -> None:
        try:
            async for raw in self._ws:
                m = json.loads(raw)
                if "id" in m:
                    fut = self._pending.pop(m["id"], None)
                    if fut and not fut.done():
                        if "error" in m:
                            fut.set_exception(CDPError(m["error"].get("message", str(m["error"]))))
                        else:
                            fut.set_result(m.get("result", {}))
                else:
                    for fn in self._listeners:
                        asyncio.create_task(fn(m))
        except Exception:
            pass
        finally:
            for fut in self._pending.values():
                if not fut.done():
                    fut.set_exception(ConnectionError("browser connection closed"))
            self._pending.clear()
            self.closed.set()

    async def close(self) -> None:
        if self._ws:
            await self._ws.close()
