"""Detect the sensitive marker in prompts and in text / PDF / image files."""
from __future__ import annotations

import io
import os
import re
from dataclasses import dataclass

from . import ocr

MARKER = "WALLED_SENSITIVE"
# PDF text extraction and OCR can mangle the underscore or insert spaces, so those paths are tolerant.
_TOLERANT = re.compile(r"WALLED[\s_\-.]*S?[\s_\-.]*SENSITIVE", re.I)
_IMAGE_EXT = {".png", ".jpg", ".jpeg", ".gif", ".webp", ".bmp", ".tif", ".tiff"}
MAX_PDF_IMAGES = 10


@dataclass
class ScanResult:
    flagged: bool
    kind: str            # text | pdf | image
    note: str = ""
    unscanned: bool = False


def contains_marker(text: str, tolerant: bool = False) -> bool:
    if tolerant:
        return bool(_TOLERANT.search(text))
    return MARKER.lower() in text.lower()


def sniff(name: str, mime: str, data: bytes) -> str:
    ext = os.path.splitext(name.lower())[1]
    if b"%PDF-" in data[:1024]:
        return "pdf"
    if (data.startswith((b"\x89PNG\r\n\x1a\n", b"\xff\xd8\xff", b"GIF87a", b"GIF89a", b"II*\x00", b"MM\x00*"))
            or (data[:4] == b"RIFF" and data[8:12] == b"WEBP")
            or mime.startswith("image/") or ext in _IMAGE_EXT):
        return "image"
    return "text"


def decode_text(data: bytes) -> str:
    if data[:2] in (b"\xff\xfe", b"\xfe\xff"):
        return data.decode("utf-16", errors="ignore")
    try:
        return data.decode("utf-8-sig")
    except UnicodeDecodeError:
        return data.decode("latin-1")


def _scan_pdf(data: bytes) -> ScanResult:
    try:
        from pypdf import PdfReader
        reader = PdfReader(io.BytesIO(data))
        if reader.is_encrypted:
            try:
                reader.decrypt("")
            except Exception:
                pass
        text = "\n".join((p.extract_text() or "") for p in reader.pages)
        if contains_marker(text, tolerant=True) or contains_marker(str(reader.metadata or ""), True):
            return ScanResult(True, "pdf", "text layer")
        if ocr.available():  # scanned PDFs: OCR the embedded page images
            count = 0
            for page in reader.pages:
                try:
                    images = list(page.images)
                except Exception:
                    continue
                for img in images:
                    if count >= MAX_PDF_IMAGES:
                        break
                    count += 1
                    t = ocr.ocr_bytes(img.data)
                    if t and contains_marker(t, tolerant=True):
                        return ScanResult(True, "pdf", "embedded image (OCR)")
            return ScanResult(False, "pdf", f"checked text + {count} embedded image(s)")
        return ScanResult(False, "pdf", "text layer only (no OCR available)")
    except Exception as exc:  # corrupt/odd PDF: fall back to a raw byte search
        return ScanResult(contains_marker(data.decode("latin-1")), "pdf", f"parse error: {exc}")


def scan_file(name: str, mime: str, data: bytes) -> ScanResult:
    kind = sniff(name, mime, data)
    if kind == "pdf":
        return _scan_pdf(data)
    if kind == "image":
        text = ocr.ocr_bytes(data)
        if text is None:
            return ScanResult(False, "image", "OCR unavailable or image unreadable", unscanned=True)
        return ScanResult(contains_marker(text, tolerant=True), "image", f"OCR via {ocr.backend()}")
    return ScanResult(contains_marker(decode_text(data)), "text")
