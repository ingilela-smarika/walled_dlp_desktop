"""Find / launch a Chromium-based browser that exposes the DevTools port to this app only.

A dedicated profile is used (Chrome refuses remote debugging on the default profile). The
debugging port is chosen by the OS (port 0) and bound to 127.0.0.1; it is read back from
the profile's DevToolsActivePort file, so nothing listens on a well-known port.
"""
from __future__ import annotations

import os
import platform
import shlex
import shutil
import subprocess
import time
import json
from pathlib import Path
from urllib.error import URLError
from urllib.parse import urlsplit
from urllib.request import urlopen

PROFILE_DIR = Path(os.environ.get("WALLED_PROFILE") or Path.home() / ".walled_dlp" / "browser-profile")


def find_browser() -> str | None:
    env = os.environ.get("WALLED_CHROME")
    if env:
        return env
    system = platform.system()
    cands: list[str] = []
    if system == "Windows":
        for base in (os.environ.get("ProgramFiles"), os.environ.get("ProgramFiles(x86)"),
                     os.environ.get("LocalAppData")):
            if base:
                cands += [rf"{base}\Google\Chrome\Application\chrome.exe",
                          rf"{base}\Microsoft\Edge\Application\msedge.exe"]
    elif system == "Darwin":
        cands = ["/Applications/Google Chrome.app/Contents/MacOS/Google Chrome",
                 "/Applications/Microsoft Edge.app/Contents/MacOS/Microsoft Edge"]
    else:
        cands = [shutil.which(n) or "" for n in ("google-chrome", "google-chrome-stable", "chromium",
                                                 "chromium-browser", "microsoft-edge")]
    return next((c for c in cands if c and os.path.exists(c)), None)


def read_ws_url() -> str | None:
    f = PROFILE_DIR / "DevToolsActivePort"
    try:
        port, path = f.read_text().split()[:2]
        return f"ws://127.0.0.1:{port}{path}"
    except (OSError, ValueError):
        return None


def _active_ws_url() -> str | None:
    candidate = read_ws_url()
    if not candidate:
        return None
    try:
        parsed = urlsplit(candidate)
        if parsed.scheme != "ws" or parsed.hostname != "127.0.0.1" or not parsed.port:
            return None
        with urlopen(f"http://127.0.0.1:{parsed.port}/json/version", timeout=1) as response:
            ws_url = json.load(response).get("webSocketDebuggerUrl")
        endpoint = urlsplit(ws_url)
        if endpoint.scheme == "ws" and endpoint.hostname in ("127.0.0.1", "localhost"):
            return ws_url
    except (OSError, ValueError, URLError, TypeError):
        pass
    return None


def _profile_in_use() -> bool:
    if platform.system() != "Windows":
        return False
    lockfile = PROFILE_DIR / "lockfile"
    if not lockfile.exists():
        return False
    try:
        fd = os.open(lockfile, os.O_RDONLY | getattr(os, "O_BINARY", 0))
    except PermissionError:
        return True
    except FileNotFoundError:
        return False
    else:
        os.close(fd)
        return False


def launch(url: str, timeout: float = 25) -> str:
    exe = find_browser()
    if not exe:
        raise RuntimeError("No Chrome/Edge found. Install Chrome or set WALLED_CHROME to its path.")
    PROFILE_DIR.mkdir(parents=True, exist_ok=True)
    ws = _active_ws_url()
    if ws:
        return ws
    if _profile_in_use():
        raise RuntimeError(
            "The Walled DLP browser profile is already in use, but its DevTools connection is unavailable. "
            "Close the existing Walled DLP browser window and try again."
        )
    (PROFILE_DIR / "DevToolsActivePort").unlink(missing_ok=True)
    args = [exe, f"--user-data-dir={PROFILE_DIR}", "--remote-debugging-port=0", "--no-first-run",
            "--no-default-browser-check", *shlex.split(os.environ.get("WALLED_CHROME_ARGS", "")), url]
    process = subprocess.Popen(args, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    deadline = time.time() + timeout
    while time.time() < deadline:
        ws = _active_ws_url()
        if ws:
            return ws
        if process.poll() is not None:
            raise RuntimeError(
                f"Browser exited with status {process.returncode} before exposing its DevTools port."
            )
        time.sleep(0.25)
    if _profile_in_use():
        raise RuntimeError(
            "The Walled DLP browser profile is in use, but Chrome did not expose its DevTools port. "
            "Close the existing browser window and try again."
        )
    raise RuntimeError("Browser started but did not expose its debugging port.")
