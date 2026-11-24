"""Asyncio backend (runs in a worker thread): launches/attaches the browser and runs the monitor."""
from __future__ import annotations

import asyncio
import logging
import threading
from typing import Callable

from . import browser, config
from .cdp import CDPConnection
from .engine import Engine
from .monitor import Monitor

log = logging.getLogger("walled.backend")


class Backend:
    def __init__(self, confirm_fn, log_event: Callable[[str], None], set_status: Callable[[str], None]):
        self.loop = asyncio.new_event_loop()
        self.engine = Engine(confirm_fn(self.loop), log_event)
        self.log_event, self.set_status = log_event, set_status
        self.conn: CDPConnection | None = None
        self._lock: asyncio.Lock | None = None
        threading.Thread(target=self._run, daemon=True, name="walled-backend").start()

    def _run(self) -> None:
        asyncio.set_event_loop(self.loop)
        self._lock = asyncio.Lock()
        self.loop.run_forever()

    def open_site(self, name: str) -> None:
        """Thread-safe: called from the UI."""
        asyncio.run_coroutine_threadsafe(self._open(config.SITES[name]["url"]), self.loop)

    async def _open(self, url: str) -> None:
        async with self._lock:
            try:
                if self.conn and not self.conn.closed.is_set():
                    await self.conn.send("Target.createTarget", {"url": url})
                    return
                ws = browser.read_ws_url()
                if ws:   # browser from an earlier run may still be open
                    try:
                        await self._attach(ws)
                        await self.conn.send("Target.createTarget", {"url": url})
                        return
                    except Exception:  # noqa: BLE001
                        self.conn = None
                ws = await asyncio.to_thread(browser.launch, url)
                await self._attach(ws)
            except Exception as exc:  # noqa: BLE001
                log.exception("open failed")
                self.log_event(f"ERROR: {exc}")
                self.set_status("Browser: error (see log)")

    async def _attach(self, ws_url: str) -> None:
        conn = CDPConnection()
        await conn.connect(ws_url)
        await Monitor(conn, self.engine.decide).start()
        self.conn = conn
        self.set_status("Browser: connected and monitored")
        self.log_event("Connected to browser; monitoring ChatGPT and Perplexity tabs.")
        asyncio.create_task(self._watch(conn))

    async def _watch(self, conn: CDPConnection) -> None:
        await conn.closed.wait()
        if self.conn is conn:
            self.conn = None
            self.set_status("Browser: closed - press Open to start again")
            self.log_event("Browser disconnected.")
