import asyncio
import base64

import make_samples
from walled_dlp import scanner
from walled_dlp.engine import Engine, extract_preview

F = make_samples.OUT


def test_marker_detection():
    assert scanner.contains_marker("x WALLED_SENSITIVE y") and scanner.contains_marker("walled_sensitive")
    assert not scanner.contains_marker("walled and sensitive")
    assert scanner.contains_marker("WALLED_S SENSITIVE", tolerant=True)


def test_text_and_pdf_files():
    s = lambda n: scanner.scan_file(n, "", (F / n).read_bytes())  # noqa: E731
    assert s("note_marker.txt").flagged and not s("note_clean.txt").flagged
    assert s("report_marker.pdf").flagged and not s("report_clean.pdf").flagged


def test_preview_extraction():
    body = '{"action":"next","messages":[{"content":{"parts":["my WALLED_SENSITIVE text"]}}],"model":"x"}'
    assert extract_preview(body) == "my WALLED_SENSITIVE text"
    assert "WALLED_SENSITIVE" in extract_preview("plain body WALLED_SENSITIVE here")


def run_engine(msg, answer):
    calls = []

    async def confirm(kind, names, preview):
        calls.append((kind, names, preview))
        return answer

    return asyncio.run(Engine(confirm).decide(msg)), calls


def test_engine_prompt_and_file_decisions():
    assert run_engine({"kind": "prompt", "text": "hi"}, False) == ("allow", [])
    v, calls = run_engine({"kind": "prompt", "source": "dom", "text": "a WALLED_SENSITIVE"}, False)
    assert v == "cancel" and calls[0][0] == "prompt"
    data = base64.b64encode((F / "note_marker.txt").read_bytes()).decode()
    v, calls = run_engine({"kind": "file", "name": "n.txt", "data": data}, True)
    assert v == "allow" and calls[0][:2] == ("file", ["n.txt"])
    clean = base64.b64encode(b"nothing here").decode()
    assert run_engine({"kind": "file", "name": "c.txt", "data": clean}, False) == ("allow", [])


def test_engine_fails_closed_on_bad_input():
    v, _ = run_engine({"kind": "file", "name": "x", "data": "!!!notbase64"}, True)
    assert v == "cancel"
