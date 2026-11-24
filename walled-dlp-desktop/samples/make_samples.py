"""Create demo files: *_marker.* contain WALLED_SENSITIVE, *_clean.* do not.
Run: python samples/make_samples.py   (writes to ./samples/files)"""
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

OUT = Path(__file__).parent / "files"
OUT.mkdir(exist_ok=True)


def pdf_bytes(text: str) -> bytes:
    """Hand-built single-page PDF with a real text layer."""
    stream = f"BT /F1 24 Tf 72 700 Td ({text}) Tj ET".encode()
    objs = [b"<< /Type /Catalog /Pages 2 0 R >>",
            b"<< /Type /Pages /Kids [3 0 R] /Count 1 >>",
            b"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 612 792] /Contents 4 0 R "
            b"/Resources << /Font << /F1 5 0 R >> >> >>",
            b"<< /Length %d >>\nstream\n" % len(stream) + stream + b"\nendstream",
            b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>"]
    out, offsets = b"%PDF-1.4\n", []
    for i, o in enumerate(objs, 1):
        offsets.append(len(out))
        out += b"%d 0 obj\n" % i + o + b"\nendobj\n"
    xref = len(out)
    out += b"xref\n0 %d\n0000000000 65535 f \n" % (len(objs) + 1)
    out += b"".join(b"%010d 00000 n \n" % o for o in offsets)
    return out + b"trailer\n<< /Size %d /Root 1 0 R >>\nstartxref\n%d\n%%%%EOF\n" % (len(objs) + 1, xref)


def image(text: str, path: Path) -> None:
    img = Image.new("RGB", (900, 200), "white")
    ImageDraw.Draw(img).text((30, 70), text, fill="black", font=ImageFont.load_default(size=48))
    img.save(path)


for tag, body in (("marker", "Quarterly numbers - WALLED_SENSITIVE - internal only"),
                  ("clean", "Quarterly numbers - nothing secret here")):
    (OUT / f"note_{tag}.txt").write_text(body + "\n")
    (OUT / f"report_{tag}.pdf").write_bytes(pdf_bytes(body))
    image(body.split(" - ", 1)[1] if tag == "marker" else body, OUT / f"screenshot_{tag}.png")
print("wrote", *sorted(p.name for p in OUT.iterdir()), sep="\n  ")
