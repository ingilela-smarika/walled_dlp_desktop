// Walled DLP - page hook. Injected by the desktop app through the Chrome DevTools Protocol
// (Page.addScriptToEvaluateOnNewDocument), i.e. it runs BEFORE any page script. Not an extension.
//
//  1. Network hook: wraps fetch / XMLHttpRequest. Any request body that is a file (File, Blob,
//     FormData part, ArrayBuffer) or text containing the marker is held until the desktop app
//     answers. "cancel" makes the request fail like a network error; nothing is transmitted.
//  2. Prompt gate: capture-phase Enter / Send-click handler so that Cancel leaves the user's text
//     in the box (clean UX). The network hook still catches anything this misses.
//  3. Response colour: constructable stylesheet (not blocked by page CSP).
(() => {
  "use strict";
  const CFG = __WALLED_CONFIG__;
  if (window.__walledInstalled || !CFG.hosts.includes(location.hostname)) return;
  // Stay completely out of Cloudflare's "Just a moment..." bot check: it is served with 403/503 (or under
  // /cdn-cgi/). The real site arrives with a fresh page load once the check passes, and is hooked then.
  try {
    const nav = performance.getEntriesByType("navigation")[0];
    if (location.pathname.startsWith("/cdn-cgi/") || (nav && (nav.responseStatus === 403 || nav.responseStatus === 503))) return;
  } catch (e) { /* ignore */ }
  window.__walledInstalled = true;

  const MARKER_RE = new RegExp(CFG.marker, "i");
  const pending = new Map();
  let seq = 0;
  // After the user clicks Continue, the SAME content may pass the network hook for a short time
  // (the site sends it right after we release the click). Matching is on the text around the marker,
  // normalised (alphanumerics only, JSON escapes removed) so prompt-box text matches the JSON body.
  // A different sensitive request is never covered by an earlier approval.
  let approvals = [];
  const norm = (s) => s.replace(/\\[nrt]/g, "").toLowerCase().replace(/[^a-z0-9]/g, "");
  function markerStrings(text) {   // JSON body -> the user-visible string(s) that hold the marker
    const out = [];
    try {
      (function walk(o) {
        if (typeof o === "string") { if (MARKER_RE.test(o)) out.push(o); }
        else if (Array.isArray(o)) o.forEach(walk);
        else if (o && typeof o === "object") Object.values(o).forEach(walk);
      })(JSON.parse(text));
    } catch (e) { /* not JSON */ }
    return out.length ? out : [text];
  }
  function approveText(text) {
    const m = norm(CFG.marker);
    for (const str of markerStrings(text)) {
      const n = norm(str), i = n.indexOf(m);
      if (i >= 0) approvals.push({ key: n.slice(Math.max(0, i - 20), i + m.length + 20), until: Date.now() + 15000 });
    }
  }
  function isApproved(text) {
    const n = norm(text), now = Date.now();
    approvals = approvals.filter((a) => a.until > now);
    return approvals.some((a) => n.includes(a.key));
  }
  let bypass = false;

  window.__walledResolve = (id, verdict) => {
    const r = pending.get(id);
    if (r) { pending.delete(id); r(verdict); }
  };

  function toast(msg, ms = 4500) {
    try {
      const el = document.createElement("div");
      el.textContent = "Walled DLP: " + msg;
      el.style.cssText = "position:fixed;right:16px;bottom:16px;z-index:2147483647;max-width:340px;" +
        "background:#222;color:#fff;padding:10px 14px;border-radius:8px;font:13px system-ui;" +
        "box-shadow:0 2px 10px rgba(0,0,0,.4)";
      (document.body || document.documentElement).appendChild(el);
      setTimeout(() => el.remove(), ms);
    } catch (e) { /* never break the page */ }
  }

  // Ask the desktop app. Resolves 'allow' | 'cancel' | 'unavailable'.
  function bridge(msg) {
    return new Promise((resolve) => {
      if (typeof window.walledBridge !== "function") return resolve("unavailable");
      const id = ++seq;
      pending.set(id, resolve);
      try { window.walledBridge(JSON.stringify({ id, host: location.hostname, ...msg })); }
      catch (e) { pending.delete(id); resolve("unavailable"); }
    });
  }

  function finalVerdict(v, what) {
    if (v === "unavailable") {
      if (CFG.failClosed) { toast("desktop app not reachable - " + what + " blocked."); return "cancel"; }
      return "allow";
    }
    return v;
  }

  bridge({ kind: "hello", url: location.href });   // shows "Hook active" in the app log

  // ---------- request body analysis ----------
  const blobToB64 = (blob) => new Promise((res, rej) => {
    const fr = new FileReader();
    fr.onload = () => res(String(fr.result).split(",", 2)[1] || "");
    fr.onerror = () => rej(fr.error);
    fr.readAsDataURL(blob);
  });
  const urlOf = (input) => typeof input === "string" ? input : (input && (input.href || input.url)) || "";
  const nameFromUrl = (u) => { try { return new URL(u, location.href).pathname.split("/").pop() || "upload"; }
                               catch (e) { return "upload"; } };

  function parts(body) {
    const texts = [], files = [];
    if (body == null) return { texts, files };
    if (typeof body === "string") texts.push(body);
    else if (body instanceof URLSearchParams) texts.push(body.toString());
    else if (body instanceof FormData) {
      for (const [, v] of body.entries()) (typeof v === "string" ? texts : files).push(v);
    } else if (body instanceof Blob) files.push(body);
    else if (body instanceof ArrayBuffer || ArrayBuffer.isView(body)) files.push(new Blob([body]));
    return { texts, files };          // ReadableStream bodies cannot be inspected
  }

  async function gate(url, method, body) {
    let p;
    try { p = parts(body); } catch (e) { return "allow"; }
    for (const text of p.texts) {
      if (!MARKER_RE.test(text)) continue;
      if (isApproved(text)) continue;                           // same content just approved
      const v = finalVerdict(await bridge({ kind: "prompt", source: "network", url, text: text.slice(0, 2e6) }),
                             "request with sensitive marker");
      if (v !== "allow") return "cancel";
      approveText(text);
    }
    for (const f of p.files) {
      if (!f.size) continue;
      if (f.size > CFG.maxFileBytes) { toast("file too large to scan (" + (f.name || "upload") + ") - not inspected."); continue; }
      let data;
      try { data = await blobToB64(f); } catch (e) { continue; }
      const v = finalVerdict(await bridge({ kind: "file", source: "network", url, name: f.name || nameFromUrl(url),
                                            mime: f.type || "", size: f.size, data }), "file upload");
      if (v !== "allow") return "cancel";
    }
    return "allow";
  }

  // ---------- fetch / XHR hooks ----------
  const origFetch = window.fetch;
  window.fetch = async function (input, init) {
    let verdict = "allow";
    try {
      const method = String((init && init.method) || (input instanceof Request ? input.method : "GET")).toUpperCase();
      if (method !== "GET" && method !== "HEAD" && method !== "OPTIONS") {
        let body = init && init.body !== undefined ? init.body : null;
        if (body == null && input instanceof Request) body = await input.clone().blob();
        verdict = await gate(urlOf(input), method, body);
      }
    } catch (e) { console.debug("[walled] inspect error", e); }
    if (verdict === "cancel") throw new TypeError("Blocked by Walled DLP: sensitive content");
    return origFetch.apply(this, arguments);
  };

  const xOpen = XMLHttpRequest.prototype.open, xSend = XMLHttpRequest.prototype.send;
  XMLHttpRequest.prototype.open = function (method, url, async) {
    this.__walled = { method: String(method).toUpperCase(), url: String(url), async: async !== false };
    return xOpen.apply(this, arguments);
  };
  XMLHttpRequest.prototype.send = function (body) {
    const meta = this.__walled, xhr = this, args = arguments;
    if (!meta || !meta.async || body == null || meta.method === "GET" || meta.method === "HEAD")
      return xSend.apply(xhr, args);
    gate(meta.url, meta.method, body).then((v) => {
      try {
        if (v === "cancel") { try { xhr.abort(); } catch (e) {} xhr.dispatchEvent(new ProgressEvent("error")); }
        else xSend.apply(xhr, args);
      } catch (e) { console.debug("[walled] send error", e); }
    });
  };

  // ---------- prompt gate (clean cancel UX) ----------
  const readBox = (b) => (b.value !== undefined ? b.value : (b.innerText ?? b.textContent)) || "";
  function composersWithMarker() {
    const out = [];
    for (const sel of CFG.composerSelectors)
      document.querySelectorAll(sel).forEach((b) => { if (!out.includes(b) && MARKER_RE.test(readBox(b))) out.push(b); });
    return out;
  }
  let promptBusy = false;
  async function guardPrompt(text, box) {
    if (promptBusy) return;
    promptBusy = true;
    try {
      const v = finalVerdict(await bridge({ kind: "prompt", source: "dom", text }), "prompt with sensitive marker");
      if (v === "allow") {
        approveText(text);
        bypass = true;
        try {
          const btn = document.querySelector(CFG.sendButtonSelector);
          if (btn && !btn.disabled) btn.click();
          else box.dispatchEvent(new KeyboardEvent("keydown",
            { key: "Enter", code: "Enter", keyCode: 13, which: 13, bubbles: true, cancelable: true }));
        } finally { bypass = false; }
      } else { toast("prompt blocked - you can edit it and try again."); box.focus && box.focus(); }
    } finally { promptBusy = false; }
  }
  window.addEventListener("keydown", (e) => {
    if (bypass || e.key !== "Enter" || e.shiftKey || e.isComposing || !e.target.closest) return;
    const box = e.target.closest("textarea, [contenteditable='true'], [role='textbox']");
    if (!box || !MARKER_RE.test(readBox(box)) || isApproved(readBox(box))) return;  // clean/approved: untouched
    e.preventDefault(); e.stopImmediatePropagation();
    guardPrompt(readBox(box), box);
  }, true);
  window.addEventListener("click", (e) => {
    if (bypass || !e.target.closest || !e.target.closest(CFG.sendButtonSelector)) return;
    const boxes = composersWithMarker().filter((b) => !isApproved(readBox(b)));
    if (!boxes.length) return;
    e.preventDefault(); e.stopImmediatePropagation();
    guardPrompt(readBox(boxes[0]), boxes[0]);
  }, true);

  // ---------- response colour ----------
  // Two layers so site CSS cannot win: (1) a constructable stylesheet (not blocked by page CSP),
  // (2) the colour set directly on every answer element with !important, re-applied as the
  // answer streams in or the page re-renders.
  const SEL = CFG.responseSelectors.join(",");
  const css = CFG.responseSelectors.map((s) => `${s}, ${s} *`).join(",\n") +
    ` { color: ${CFG.responseColor} !important; }`;
  function applySheet() {
    try {
      if (!window.__walledSheet) { const sh = new CSSStyleSheet(); sh.replaceSync(css); window.__walledSheet = sh; }
      if (!document.adoptedStyleSheets.includes(window.__walledSheet))
        document.adoptedStyleSheets = [...document.adoptedStyleSheets, window.__walledSheet];
    } catch (e) {
      if (!document.getElementById("walled-style")) {
        const s = document.createElement("style"); s.id = "walled-style"; s.textContent = css;
        (document.head || document.documentElement).appendChild(s);
      }
    }
  }
  function paint() {
    try {
      document.querySelectorAll(SEL).forEach((el) => {
        el.style.setProperty("color", CFG.responseColor, "important");
        el.querySelectorAll("*").forEach((c) => c.style.setProperty("color", CFG.responseColor, "important"));
      });
    } catch (e) { /* never break the page */ }
  }
  let paintTimer = null;
  const schedulePaint = () => { if (!paintTimer) paintTimer = setTimeout(() => { paintTimer = null; applySheet(); paint(); }, 200); };
  applySheet();
  new MutationObserver(schedulePaint).observe(document, { childList: true, subtree: true, characterData: true });
  document.addEventListener("DOMContentLoaded", schedulePaint);
  setInterval(schedulePaint, 2000);

  // Troubleshooting: type  __walledDebug()  in the page console.
  window.__walledDebug = () => {
    const first = document.querySelector(SEL);
    const target = first && (first.querySelector("p, li, span") || first);
    return { installed: true, host: location.hostname, sheets: document.adoptedStyleSheets.length,
             matches: Object.fromEntries(CFG.responseSelectors.map((s) => [s, document.querySelectorAll(s).length])),
             sampleColour: target ? getComputedStyle(target).color : null };
  };
})();
