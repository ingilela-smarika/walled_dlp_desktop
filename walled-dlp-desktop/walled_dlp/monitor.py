"""Attaches to every browser tab, injects inject.js, and answers its scan requests.

Flow: page script -> Runtime binding `walledBridge` -> Monitor -> decider (engine) -> verdict
-> Runtime.evaluate(window.__walledResolve(id, verdict)) back into the same tab.
"""
from __future__ import annotations

import asyncio
import json
import logging
from urllib.parse import urlparse
from importlib import resources
from typing import Awaitable, Callable

from . import config
from .cdp import CDPConnection

log = logging.getLogger("walled.monitor")
BINDING = "walledBridge"


def load_inject_source() -> str:
    js = resources.files("walled_dlp").joinpath("inject.js").read_text(encoding="utf-8")
    return js.replace("__WALLED_CONFIG__", json.dumps(config.page_config()))


class Monitor:
    def __init__(self, conn: CDPConnection, decider: Callable[[dict], Awaitable[str]]):
        self.conn, self.decider = conn, decider
        self.source = load_inject_source()
        self.sessions: dict[str, str] = {}   # sessionId -> targetId

    async def start(self) -> None:
        self.conn.on_event(self._dispatch)
        await self.conn.send("Target.setAutoAttach",
                             {"autoAttach": True, "waitForDebuggerOnStart": True, "flatten": True})
        for t in (await self.conn.send("Target.getTargets"))["targetInfos"]:
            if t["type"] == "page" and not t.get("attached"):
                await self.conn.send("Target.attachToTarget", {"targetId": t["targetId"], "flatten": True})

    async def _dispatch(self, m: dict) -> None:
        method, p = m.get("method"), m.get("params", {})
        try:
            if method == "Target.attachedToTarget":
                await self._setup(p["sessionId"], p["targetInfo"], p.get("waitingForDebugger", False))
            elif method == "Target.detachedFromTarget":
                self.sessions.pop(p.get("sessionId"), None)
            elif method == "Runtime.bindingCalled" and p.get("name") == BINDING:
                await self._on_binding(m.get("sessionId"), p["payload"])
        except Exception:  # noqa: BLE001
            log.exception("monitor event error")

    async def _setup(self, sid: str, info: dict, waiting: bool) -> None:
        if info.get("type") != "page" or sid in self.sessions:
            if waiting:   # not ours (worker, iframe...): just let it run
                await self.conn.send("Runtime.runIfWaitingForDebugger", {}, sid)
            return
        self.sessions[sid] = info["targetId"]
        send = lambda method, params=None: self.conn.send(method, params, sid)  # noqa: E731
        setup = [send("Page.enable"), send("Runtime.enable"), send("Runtime.addBinding", {"name": BINDING}),
                 send("Page.addScriptToEvaluateOnNewDocument",
                      {"source": self.source, "runImmediately": True})]
        if waiting:
            # A paused tab only answers some commands after it resumes, so issue the setup and the
            # resume together, in order (the connection preserves ordering). The hook is registered
            # before the first page script can run.
            await asyncio.gather(*setup, send("Runtime.runIfWaitingForDebugger"))
        else:
            await asyncio.gather(*setup)
            if (info.get("title", "").lower().startswith("just a moment")):
                pass   # Cloudflare check in progress: do not disturb it; the hook installs on the next load
            elif urlparse(info.get("url", "")).hostname in config.allowed_hosts():
                # The site loaded before we attached (e.g. restored tab), so its scripts may already
                # hold the original fetch. Reload so our hook is installed before any page script.
                await send("Page.reload")
            else:
                await send("Runtime.evaluate", {"expression": self.source})

    async def _on_binding(self, sid: str | None, payload: str) -> None:
        msg = json.loads(payload)
        try:
            verdict = await self.decider(msg)
        except Exception:  # noqa: BLE001
            log.exception("decider failed; blocking")
            verdict = "cancel"
        expr = f"window.__walledResolve({int(msg['id'])}, {json.dumps(verdict)})"
        try:
            await self.conn.send("Runtime.evaluate", {"expression": expr}, sid)
        except Exception:  # tab closed while the dialog was open
            log.info("tab gone before verdict could be delivered")
