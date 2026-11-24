# Walled DLP - desktop guard for ChatGPT & Perplexity

A **standalone desktop application** (Python + Tk, no browser extension/plugin) that watches the ChatGPT and Perplexity web apps, inspects outgoing **prompts and file uploads** (text, PDF, image) for the marker `WALLED_SENSITIVE`, and shows a **native desktop dialog** (Continue / Cancel) before anything sensitive is sent. It also recolours AI responses on both sites.

Demo video: `<add link>`

## How it works

```
 Walled DLP desktop app (Python)                         Chrome/Edge window launched by the app
 ┌───────────────────────────────────────────┐          ┌────────────────────────────────────────────┐
 │ ui.py       Tk window + native dialog     │          │ chatgpt.com / perplexity.ai tab            │
 │ backend.py  launches browser, runs monitor│  CDP     │  inject.js (runs before any page script)   │
 │ monitor.py  attaches to every tab ────────┼─────────►│   • wraps fetch / XMLHttpRequest           │
 │ engine.py   decides allow / cancel        │◄─────────┼─   • prompt gate (Enter / Send)            │
 │ scanner.py  text · PDF · image OCR        │  verdict │   • response colour stylesheet             │
 └───────────────────────────────────────────┘          └────────────────────────────────────────────┘
```

* **Not an extension.** The app starts a Chromium browser with a private DevTools (CDP) port bound to 127.0.0.1 (random port, dedicated profile) and attaches to it. Through CDP it injects `inject.js` with `Page.addScriptToEvaluateOnNewDocument`, so the hook runs before the sites' own code.
* **Interception happens at the point of transmission.** The hook wraps `fetch` and `XMLHttpRequest`. Request bodies (strings, `File`/`Blob`, `FormData` parts, `ArrayBuffer`) are inspected; a request that contains the marker, or a file that does, is **held** until the desktop app answers. *Continue* releases the untouched request. *Cancel* makes it fail like a network error, so nothing is transmitted. Because it works on network requests, it does not depend on page selectors for correctness.
* **Prompts:** additionally, an Enter / Send-click gate stops the submit *before* the site clears the box, so after Cancel the user's text is still there to edit and resend. If this gate ever stops matching (site redesign), the network hook still catches the prompt.
* **Files** (picker, drag-drop, paste, any upload path) are caught when the site transmits them: text (UTF-8/16), **PDF** (text layer + OCR of embedded page images for scanned PDFs), **images** (OCR). File type is detected from content, not the extension.
* **Native dialog:** a Tk window (always on top) that says whether the marker was found in a *text prompt* or a *file*, shows the prompt text or the file name, and offers **Continue** / **Cancel** (focus starts on Cancel; Esc/close = Cancel). Clean prompts and files pass with no interaction.
* **Response colour:** a constructable stylesheet (not blocked by page CSP) colours assistant messages orange on both sites. Selectors are in `walled_dlp/config.py`.
* **Fail closed:** if the app is not reachable, a marker prompt / file upload is blocked (the hook shows a small notice); an internal error while deciding means Cancel.

## Setup

Requires Python 3.10+ and Google Chrome (or Edge).

```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -e .
pip install rapidocr-onnxruntime      # OCR for images/scanned PDFs (pip-only)
```
If that OCR package does not install on your Python version, use `pip install rapidocr onnxruntime`, **or** install Tesseract (UB-Mannheim build) plus `pip install pytesseract`. Without any OCR, text and PDF text-layer detection still work and images are reported as "could not inspect".

## Run

```powershell
python -m walled_dlp            # or: walled-dlp --open ChatGPT
```
1. Click **Open ChatGPT** / **Open Perplexity** in the app window. It starts the monitored browser (own profile at `~/.walled_dlp/browser-profile`).
2. **Log in once** in that browser window (the login persists in the dedicated profile).
3. Use the sites normally. Monitoring applies to tabs of the browser the app launched.

If the app reports that the browser profile is already in use, close the existing Walled DLP browser window and retry.

`python samples/make_samples.py` creates `samples/files/` (`*_marker.*` contain the marker, `*_clean.*` do not).

| Action | Expected |
|---|---|
| Prompt `hello` | sent normally |
| Prompt with `WALLED_SENSITIVE` | native dialog (type: prompt) |
| Upload `note_marker.txt` / `report_marker.pdf` / `screenshot_marker.png` | native dialog naming the file |
| Upload `*_clean.*` | uploads, no dialog |
| Continue / Cancel | proceeds unchanged / blocked (prompt text stays editable) |
| Any AI answer | text shown in orange |

## Acceptance checklist (use this as the demo script, on **both** ChatGPT and Perplexity)

Click **Check setup** first: it must report the browser found and an OCR engine OK (otherwise images cannot be inspected).

| # | Criterion | How it is met | Automated test | Live check |
|---|---|---|---|---|
| 1 | Prompt without marker: no interruption | hook only acts when the marker is present | `test_e2e` clean prompt | ☐ ChatGPT ☐ Perplexity |
| 2 | Prompt with `WALLED_SENSITIVE`: native dialog | prompt gate + network hook -> Tk dialog | `test_e2e` marker prompt (click and Enter) | ☐ ☐ |
| 3 | Text file with marker: dialog before upload | upload held at the network layer, scanned | `test_e2e` `.txt` via fetch + XHR | ☐ ☐ |
| 4 | PDF with marker: dialog before upload | `pypdf` text layer (+ OCR of embedded images) | `test_e2e` `.pdf` | ☐ ☐ |
| 5 | Image with marker: dialog before upload | OCR (Tesseract / RapidOCR) | `test_e2e` `.png` | ☐ ☐ |
| 6 | Continue: proceeds normally | held request is released untouched | `test_e2e` (prompt sent once, files sent) | ☐ ☐ |
| 7 | Cancel: blocked, can retry | prompt never sent and text kept; upload fails, nothing transmitted | `test_e2e` | ☐ ☐ |
| 8 | ChatGPT response colour changed | stylesheet on assistant messages | `test_e2e` computed colour (mock DOM) | ☐ |
| 9 | Perplexity response colour changed | same, Perplexity selectors | selector check live only | ☐ |
| 10 | Native dialog, not HTML in the browser | Tk window owned by the desktop app | full-stack run with real Tk window | ☐ |
| 11 | Inspected before transmission | request is paused until the verdict arrives | `test_e2e` (server receives nothing on Cancel) | ☐ |

If a live check fails, send the app log line (the window shows each inspected upload and decision) and adjust `walled_dlp/config.py` (selectors) - the network hook itself is site-agnostic.

## Tests

```powershell
pip install pytest aiohttp
pytest -q tests
```
* `test_unit.py` - marker detection, text/PDF scanning, preview extraction, engine decisions, fail-closed.
* `test_e2e.py` - **real Chromium** launched and attached by the app's own code against a mock chat site: hook injection into new tabs, response colour, clean prompt untouched, marker prompt Cancel (nothing sent, text kept) / Continue (sent once, one dialog) via click and Enter key, network-level safety net, text/PDF/PNG uploads via `fetch(PUT File)` and `XMLHttpRequest(FormData)` with Cancel/Continue, clean files unprompted, no re-prompt for an approved file. Skipped unless `WALLED_CHROME` points to a Chromium binary (`WALLED_CHROME_ARGS="--headless=new --no-sandbox"` in CI).

## Known limitations / trade-offs

* **Verified against a mock site, not the live services** (they need a login). The hook is generic (any fetch/XHR body), but ChatGPT/Perplexity can change their upload flow or markup. Use the table above to verify live; selectors for the prompt box, send button and response colour are isolated in `walled_dlp/config.py`.
* **Only the browser launched by the app is monitored.** Chrome does not allow a DevTools port on your normal default profile, so the app uses its own profile (log in once). Other browsers/windows are not covered.
* **Cancel on a file upload** makes the site show an upload error for that attachment (the file is never sent); remove it and attach another.
* Not covered: uploads made from Web Workers/Service Workers or cross-origin iframes, WebSocket/`sendBeacon` payloads, streamed (ReadableStream) bodies, and files over 15 MB (not scanned; a notice is shown). Office documents (docx/xlsx) are not parsed.
* OCR can miss stylised or very low-resolution text. Marker matching is case-insensitive; PDF/OCR matching also tolerates spaces/dashes instead of `_`.
* Sensitive content is analysed locally only; nothing is sent to third parties.

## Alternatives considered
A local TLS-intercepting proxy (mitmproxy) works with any browser but requires installing a root certificate; OS-level UI automation is fragile and cannot reliably block a submit; an embedded web view cannot read request bodies. CDP gives body-level visibility, hold/release control and no certificate changes.
