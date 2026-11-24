"""End-to-end: real Chromium launched and attached by the app code, against a mock chat site.

Needs a Chromium binary: set WALLED_CHROME (and WALLED_CHROME_ARGS for CI sandboxes), else skipped.
"""
import asyncio
import base64
import io
import os
import tempfile
from pathlib import Path

import pytest
from aiohttp import web
from PIL import Image, ImageDraw, ImageFont

import make_samples

CHROME = os.environ.get("WALLED_CHROME", "")
pytestmark = pytest.mark.skipif(not CHROME or not os.path.exists(CHROME), reason="no Chromium (WALLED_CHROME)")

PAGE = """<!doctype html><body>
<div id="prompt-textarea" contenteditable="true"></div>
<button data-testid="send-button" id="send">Send</button>
<div data-message-author-role="assistant"><p id="ans">The answer</p></div>
<script>
window.__last = null;
async function send(){
  const text = document.getElementById('prompt-textarea').innerText;
  try { const r = await fetch('/backend-api/conversation',{method:'POST',headers:{'content-type':'application/json'},
        body:JSON.stringify({messages:[{content:{parts:[text]}}]})}); window.__last='sent:'+r.status; }
  catch(e){ window.__last='error'; }
}
document.getElementById('send').addEventListener('click', send);
document.getElementById('prompt-textarea').addEventListener('keydown', e=>{ if(e.key==='Enter'&&!e.shiftKey){e.preventDefault(); send();} });
const toFile=(n,m,b)=>new File([Uint8Array.from(atob(b),c=>c.charCodeAt(0))],n,{type:m});
window.putFile = async (n,m,b)=>{ try{ const r=await fetch('/upload-put/'+n,{method:'PUT',body:toFile(n,m,b)}); return 'sent:'+r.status;}catch(e){return 'error';} };
window.postForm = (n,m,b)=>new Promise(res=>{ const fd=new FormData(); fd.append('key','k'); fd.append('file',toFile(n,m,b));
  const x=new XMLHttpRequest(); x.open('POST','/upload-form'); x.onload=()=>res('sent:'+x.status); x.onerror=()=>res('error'); x.send(fd); });
window.rawFetch = async (t)=>{ try{ const r=await fetch('/other',{method:'POST',body:JSON.stringify({q:t})}); return 'sent:'+r.status;}catch(e){return 'error';} };
</script></body>"""

b64 = lambda b: base64.b64encode(b).decode()  # noqa: E731


def png(text):
    img = Image.new("RGB", (900, 200), "white")
    ImageDraw.Draw(img).text((30, 70), text, fill="black", font=ImageFont.load_default(size=48))
    buf = io.BytesIO(); img.save(buf, "PNG"); return buf.getvalue()


async def until(cond, timeout=10):
    end = asyncio.get_running_loop().time() + timeout
    while asyncio.get_running_loop().time() < end:
        if cond():
            return True
        await asyncio.sleep(0.05)
    return False


def test_end_to_end(monkeypatch):
    tmp = tempfile.mkdtemp()
    monkeypatch.setenv("WALLED_PROFILE", tmp)
    monkeypatch.setenv("WALLED_EXTRA_HOSTS", "127.0.0.1")
    monkeypatch.setenv("WALLED_CHROME_ARGS", os.environ.get("WALLED_CHROME_ARGS", "--headless=new --no-sandbox"))
    asyncio.run(scenario())


async def scenario():
    from walled_dlp import browser
    from walled_dlp.cdp import CDPConnection
    from walled_dlp.engine import Engine
    from walled_dlp.monitor import Monitor
    import importlib; importlib.reload(browser)

    received = []   # what actually reached the "server"

    async def rec(request):
        received.append((request.path, (await request.read())))
        return web.json_response({"ok": True})
    app = web.Application(client_max_size=50 * 1024 * 1024)
    app.router.add_get("/", lambda r: web.Response(text=PAGE, content_type="text/html"))
    app.router.add_route("*", "/{tail:.*}", rec)
    runner = web.AppRunner(app); await runner.setup()
    site = web.TCPSite(runner, "127.0.0.1", 0); await site.start()
    port = site._server.sockets[0].getsockname()[1]

    confirms, answer = [], {"v": True}

    async def confirm(kind, names, preview):
        confirms.append((kind, names, preview)); return answer["v"]
    engine = Engine(confirm)

    ws = await asyncio.to_thread(browser.launch, "about:blank")
    mconn = CDPConnection(); await mconn.connect(ws)
    await Monitor(mconn, engine.decide).start()

    drv = CDPConnection(); await drv.connect(ws)
    tid = (await drv.send("Target.createTarget", {"url": f"http://127.0.0.1:{port}/"}))["targetId"]
    sid = (await drv.send("Target.attachToTarget", {"targetId": tid, "flatten": True}))["sessionId"]

    async def ev(expr, wait=False):
        r = await drv.send("Runtime.evaluate", {"expression": expr, "returnByValue": True, "awaitPromise": wait}, sid)
        return r.get("result", {}).get("value")

    assert await until(lambda: True)
    for _ in range(100):
        if await ev("window.__walledInstalled === true"): break
        await asyncio.sleep(0.1)
    assert await ev("window.__walledInstalled === true"), "hook was not injected"

    def reset():
        received.clear(); confirms.clear()

    async def set_prompt(t):
        await ev(f"document.getElementById('prompt-textarea').textContent = {t!r}; window.__last = null")

    # --- response colour
    assert await ev("getComputedStyle(document.getElementById('ans')).color") == "rgb(232, 89, 12)"

    # --- clean prompt: untouched, no dialog
    reset(); await set_prompt("hello there"); await ev("document.getElementById('send').click()")
    assert await until(lambda: any(p == "/backend-api/conversation" for p, _ in received)) and not confirms

    # --- marker prompt + Cancel (Send click): never transmitted, text kept for editing
    reset(); answer["v"] = False; await set_prompt("secret WALLED_SENSITIVE plan")
    await ev("document.getElementById('send').click()")
    assert await until(lambda: len(confirms) == 1)
    await asyncio.sleep(0.3)
    assert confirms[0][0] == "prompt" and "WALLED_SENSITIVE" in confirms[0][2]
    assert not received and await ev("document.getElementById('prompt-textarea').textContent") == "secret WALLED_SENSITIVE plan"

    # --- same prompt, Enter key, Continue: sent exactly once, single dialog
    reset(); answer["v"] = True
    await ev("document.getElementById('prompt-textarea').focus()")
    for t in ("rawKeyDown", "keyUp"):
        await drv.send("Input.dispatchKeyEvent", {"type": t, "key": "Enter", "code": "Enter",
                       "windowsVirtualKeyCode": 13, "text": "\r" if t == "rawKeyDown" else ""}, sid)
    assert await until(lambda: any(p == "/backend-api/conversation" for p, _ in received))
    await asyncio.sleep(0.3)
    assert len(confirms) == 1 and sum(p == "/backend-api/conversation" for p, _ in received) == 1
    assert b"WALLED_SENSITIVE" in received[0][1]

    # --- network-level safety net (request that bypasses the prompt box)
    reset(); answer["v"] = False
    assert await ev("window.rawFetch('x WALLED_SENSITIVE y')", True) == "error" and not received
    answer["v"] = True
    assert await ev("window.rawFetch('x WALLED_SENSITIVE y')", True) == "sent:200" and received

    # --- content approved at network level first is not asked about again at the prompt box
    reset(); answer["v"] = True
    assert await ev("window.rawFetch('draft WALLED_SENSITIVE text')", True) == "sent:200" and len(confirms) == 1
    await set_prompt("draft WALLED_SENSITIVE text"); await ev("document.getElementById('send').click()")
    assert await until(lambda: any(p == "/backend-api/conversation" for p, _ in received)) and len(confirms) == 1
    # ...but a different sensitive prompt is still asked
    answer["v"] = False; await set_prompt("another WALLED_SENSITIVE one"); n = len(received)
    await ev("document.getElementById('send').click()"); await asyncio.sleep(0.5)
    assert len(confirms) == 2 and len(received) == n

    # --- files: (name, mime, bytes, has_marker); bytes differ per upload path (approvals are cached by content)
    def files(tag):
        return [("m.txt", "text/plain", b"Q3 numbers - WALLED_SENSITIVE - internal " + tag.encode(), True),
                ("c.txt", "text/plain", b"nothing secret " + tag.encode(), False),
                ("m.pdf", "application/pdf", make_samples.pdf_bytes(f"Q3 {tag} WALLED_SENSITIVE internal"), True),
                ("c.pdf", "application/pdf", make_samples.pdf_bytes(f"Q3 {tag} nothing secret"), False),
                ("m.png", "image/png", png(f"WALLED_SENSITIVE {tag}"), True),
                ("c.png", "image/png", png(f"nothing secret {tag}"), False)]
    for fn in ("putFile", "postForm"):                     # fetch(PUT File) and XHR(FormData)
        for name, mime, data, flagged in files(fn):
            for decision in ([False, True] if flagged else [True]):
                reset(); answer["v"] = decision
                out = await ev(f"window.{fn}({name!r},{mime!r},{b64(data)!r})", True)
                if not flagged:
                    assert out == "sent:200" and not confirms, (fn, name)
                else:
                    assert len(confirms) == 1 and confirms[0] == ("file", [name], None), (fn, name)
                    assert out == ("sent:200" if decision else "error"), (fn, name, decision)
                    assert bool(received) == decision, (fn, name, decision)
        # an approved file is not asked about again when the site re-sends it
        name, mime, data, _ = files(fn)[0]
        reset(); answer["v"] = False
        assert await ev(f"window.{fn}({name!r},{mime!r},{b64(data)!r})", True) == "sent:200" and not confirms
    print("E2E OK")
    await mconn.close(); await drv.close()
    try: await drv.send("Browser.close") 
    except Exception: pass
    await runner.cleanup()
