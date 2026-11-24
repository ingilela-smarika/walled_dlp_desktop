"""OCR backend chain: Tesseract (if installed) -> rapidocr_onnxruntime -> rapidocr -> unavailable."""
from __future__ import annotations

import importlib
import io
import os
import shutil

_engine = None  # None = not loaded, False = unavailable, else (name, handle)
_WIN_TESSERACT = [r"C:\Program Files\Tesseract-OCR\tesseract.exe",
                  r"C:\Program Files (x86)\Tesseract-OCR\tesseract.exe"]


def _load():
    global _engine
    if _engine is not None:
        return _engine
    _engine = False
    try:
        import pytesseract
        path = shutil.which("tesseract") or next((p for p in _WIN_TESSERACT if os.path.exists(p)), None)
        if path:
            pytesseract.pytesseract.tesseract_cmd = path
            pytesseract.get_tesseract_version()
            _engine = ("tesseract", pytesseract)
            return _engine
    except Exception:
        pass
    for mod in ("rapidocr_onnxruntime", "rapidocr"):
        try:
            _engine = (mod, importlib.import_module(mod).RapidOCR())
            return _engine
        except Exception:
            continue
    return _engine


def available() -> bool:
    return bool(_load())


def backend() -> str:
    e = _load()
    return e[0] if e else "none"


def ocr_bytes(data: bytes) -> str | None:
    """Return recognised text, or None if no OCR backend is available / the image is unreadable."""
    eng = _load()
    if not eng:
        return None
    try:
        from PIL import Image
        img = Image.open(io.BytesIO(data))
        img.load()
        img = img.convert("RGB")
        if min(img.size) < 600:  # small screenshots OCR much better when upscaled
            img = img.resize((img.width * 2, img.height * 2))
        name, handle = eng
        if name == "tesseract":
            return handle.image_to_string(img)
        import numpy as np
        out = handle(np.array(img))
        if name == "rapidocr_onnxruntime":
            result = out[0] if isinstance(out, tuple) else out
            return " ".join(r[1] for r in (result or []))
        return " ".join(getattr(out, "txts", None) or [])
    except Exception:
        return None
