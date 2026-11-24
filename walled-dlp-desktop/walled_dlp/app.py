"""Entry point: python -m walled_dlp [--open ChatGPT|Perplexity]"""
from __future__ import annotations

import argparse
import asyncio
import logging
import signal
import threading

from . import browser, config, ocr
from .backend import Backend
from .ui import DesktopUI


def check_setup(log) -> None:
    """Report things that would silently weaken detection (run off the UI thread: OCR loads slowly)."""
    def work():
        exe = browser.find_browser()
        log(f"Browser: {exe or 'NOT FOUND - install Chrome/Edge or set WALLED_CHROME'}")
        if ocr.available():
            log(f"OCR for images / scanned PDFs: OK ({ocr.backend()})")
        else:
            log("WARNING: no OCR engine - marker text inside IMAGES cannot be detected. "
                "Run: pip install rapidocr-onnxruntime (or install Tesseract). See README.")
    threading.Thread(target=work, daemon=True).start()


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="walled-dlp", description="DLP guard for ChatGPT and Perplexity")
    ap.add_argument("--open", choices=list(config.SITES), help="open this site on start")
    args = ap.parse_args(argv)
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(name)s %(levelname)s %(message)s")

    holder: dict = {}
    ui = DesktopUI(lambda name: holder["backend"].open_site(name), list(config.SITES),
                   on_check=lambda: check_setup(ui.log))

    def make_confirm(loop):
        async def confirm(kind, names, preview) -> bool:
            fut = loop.create_future()
            ui.request_dialog(kind, names, preview,
                              lambda ok: loop.call_soon_threadsafe(lambda: fut.done() or fut.set_result(ok)))
            return await fut
        return confirm

    holder["backend"] = Backend(make_confirm, ui.log, ui.set_status)
    ui.log("Ready. Use the buttons above to open ChatGPT or Perplexity in the monitored browser.")
    check_setup(ui.log)
    signal.signal(signal.SIGINT, lambda *_: ui.stop())
    if args.open:
        ui.root.after(300, lambda: holder["backend"].open_site(args.open))
    ui.run()
    return 0
