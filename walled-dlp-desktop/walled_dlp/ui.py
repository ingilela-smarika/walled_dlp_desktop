"""Desktop UI (Tk): control window + native confirmation dialogs. Tk lives on the main thread;
the asyncio backend talks to it through a thread-safe queue."""
from __future__ import annotations

import os
import queue
import time
import tkinter as tk
from tkinter import scrolledtext, ttk
from typing import Callable

PREVIEW_CHARS = 300


class DesktopUI:
    def __init__(self, on_open: Callable[[str], None], sites: list[str],
                 on_check: Callable[[], None] | None = None):
        self.root = tk.Tk()
        self.root.title("Walled DLP")
        self.root.geometry("640x420")
        self._q: queue.Queue = queue.Queue()
        top = ttk.Frame(self.root, padding=10)
        top.pack(fill="x")
        self.status = ttk.Label(top, text="Browser: not connected", font=("TkDefaultFont", 10, "bold"))
        self.status.pack(anchor="w")
        ttk.Label(top, text="Open the sites below from here so the app can monitor them.",
                  foreground="gray").pack(anchor="w", pady=(2, 8))
        bar = ttk.Frame(top)
        bar.pack(anchor="w")
        for name in sites:
            ttk.Button(bar, text=f"Open {name}", command=lambda n=name: on_open(n)).pack(side="left", padx=(0, 8))
        if on_check:
            ttk.Button(bar, text="Check setup", command=on_check).pack(side="left")
        self.log_box = scrolledtext.ScrolledText(self.root, state="disabled", height=14)
        self.log_box.pack(fill="both", expand=True, padx=10, pady=(0, 10))
        self.root.after(100, self._poll)

    # ---- thread-safe API ----
    def log(self, text: str) -> None:
        self._q.put(("log", text))

    def set_status(self, text: str) -> None:
        self._q.put(("status", text))

    def request_dialog(self, kind: str, names: list[str], preview: str | None, cb: Callable[[bool], None]) -> None:
        auto = os.environ.get("WALLED_DLP_AUTO")        # headless CI/testing only
        if auto:
            cb(auto.lower() == "continue")
        else:
            self._q.put(("dialog", kind, names, preview, cb))

    def run(self) -> None:
        self.root.mainloop()

    def stop(self) -> None:
        self._q.put(("quit",))

    # ---- main thread ----
    def _poll(self) -> None:
        try:
            while True:
                item = self._q.get_nowait()
                if item[0] == "log":
                    self.log_box.configure(state="normal")
                    self.log_box.insert("end", time.strftime("%H:%M:%S ") + item[1] + "\n")
                    self.log_box.see("end")
                    self.log_box.configure(state="disabled")
                elif item[0] == "status":
                    self.status.configure(text=item[1])
                elif item[0] == "dialog":
                    self._dialog(*item[1:])
                elif item[0] == "quit":
                    self.root.quit()
                    return
        except queue.Empty:
            pass
        self.root.after(100, self._poll)

    def _dialog(self, kind: str, names: list[str], preview: str | None, cb: Callable[[bool], None]) -> None:
        win = tk.Toplevel(self.root)
        win.title("Walled DLP - sensitive content detected")
        win.attributes("-topmost", True)
        done = {"v": False}

        def decide(ok: bool) -> None:
            if done["v"]:
                return
            done["v"] = True
            cb(ok)
            win.destroy()

        frm = ttk.Frame(win, padding=16)
        frm.pack(fill="both", expand=True)
        ttk.Label(frm, text="Sensitive content detected", font=("TkDefaultFont", 13, "bold")).pack(anchor="w")
        where = "a text prompt" if kind == "prompt" else "a file"
        ttk.Label(frm, text=f"The marker WALLED_SENSITIVE was found in {where}.", wraplength=420).pack(
            anchor="w", pady=(6, 10))
        label = "Prompt" if kind == "prompt" else "File"
        shown = (preview or "") if kind == "prompt" else ", ".join(names)
        if len(shown) > PREVIEW_CHARS:
            shown = shown[:PREVIEW_CHARS] + "..."
        ttk.Label(frm, text=f"{label}:", font=("TkDefaultFont", 9, "bold")).pack(anchor="w")
        ttk.Label(frm, text=shown, wraplength=420, justify="left").pack(anchor="w", pady=(0, 12))
        ttk.Label(frm, text="Continue sends it to the AI service. Cancel blocks it.", foreground="gray").pack(anchor="w")
        row = ttk.Frame(frm)
        row.pack(anchor="e", pady=(14, 0))
        cancel = ttk.Button(row, text="Cancel", command=lambda: decide(False))
        cancel.pack(side="left", padx=6)
        ttk.Button(row, text="Continue", command=lambda: decide(True)).pack(side="left")
        win.protocol("WM_DELETE_WINDOW", lambda: decide(False))
        win.bind("<Escape>", lambda e: decide(False))
        win.update_idletasks()
        w, h = 480, win.winfo_reqheight()
        win.geometry(f"{w}x{h}+{(win.winfo_screenwidth() - w) // 2}+{(win.winfo_screenheight() - h) // 3}")
        win.lift()
        win.focus_force()
        cancel.focus_set()   # default focus on the safe choice
        win.bell()
