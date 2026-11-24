"""Decision engine: inspect what the page hook sends, ask the user if it is sensitive."""
from __future__ import annotations

import asyncio
import base64
import hashlib
import json
import logging
import time
from typing import Awaitable, Callable

from . import scanner

log = logging.getLogger("walled.engine")
ALLOW_TTL = 600  # seconds a user-approved file stays approved (the site may re-send/re-check it)

Confirm = Callable[[str, list, "str | None"], Awaitable[bool]]


def snippet(text: str, width: int = 200) -> str:
    i = text.lower().find(scanner.MARKER.lower())
    if i < 0:
        return text[:width]
    start = max(0, i - width // 2)
    return ("..." if start else "") + text[start:start + width] + ("..." if start + width < len(text) else "")


def extract_preview(body: str) -> str:
    """Network bodies are usually JSON: show the user-visible string, not the whole payload."""
    try:
        obj = json.loads(body)
    except ValueError:
        return snippet(body)
    hits: list[str] = []

    def walk(o):
        if isinstance(o, str):
            if scanner.contains_marker(o):
                hits.append(o)
        elif isinstance(o, dict):
            for v in o.values():
                walk(v)
        elif isinstance(o, list):
            for v in o:
                walk(v)

    walk(obj)
    return min(hits, key=len) if hits else snippet(body)


class Engine:
    def __init__(self, confirm: Confirm, log_event: Callable[[str], None] = lambda s: None):
        self.confirm, self.log_event = confirm, log_event
        self._approved: dict[str, float] = {}

    async def decide(self, msg: dict) -> str:
        """Return 'allow' or 'cancel'. Any internal error -> 'cancel' (fail closed)."""
        site = msg.get("host", "?")
        try:
            if msg.get("kind") == "hello":
                self.log_event(f"Hook active on {site}")
                return "allow"
            if msg.get("kind") == "prompt":
                text = msg.get("text", "")
                if not scanner.contains_marker(text):
                    return "allow"
                preview = text if msg.get("source") == "dom" else extract_preview(text)
                ok = await self.confirm("prompt", ["Text prompt"], preview)
                self.log_event(f"[{site}] prompt with marker -> {'Continue' if ok else 'Cancel'}")
                return "allow" if ok else "cancel"
            if msg.get("kind") == "file":
                name = msg.get("name") or "unnamed"
                data = base64.b64decode(msg.get("data", ""))
                key = hashlib.sha256(data).hexdigest()
                if self._approved.get(key, 0) > time.time():
                    return "allow"
                res = await asyncio.get_running_loop().run_in_executor(
                    None, scanner.scan_file, name, msg.get("mime", ""), data)
                if res.unscanned:
                    self.log_event(f"[{site}] WARNING could not inspect {name} ({res.note})")
                if not res.flagged:
                    self.log_event(f"[{site}] file {name}: clean ({res.kind})")
                    return "allow"
                ok = await self.confirm("file", [name], None)
                self.log_event(f"[{site}] file {name} has marker ({res.note}) -> "
                               f"{'Continue' if ok else 'Cancel'}")
                if ok:
                    self._approved[key] = time.time() + ALLOW_TTL
                return "allow" if ok else "cancel"
            return "allow"
        except Exception:  # noqa: BLE001
            log.exception("engine error")
            return "cancel"
