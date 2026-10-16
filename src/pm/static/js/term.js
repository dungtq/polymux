/* Read-only terminal viewer. No eval, no inline handlers, never sends input.
 *
 * CSP note: xterm sets element.style.* (CSSOM, allowed) but its DOM renderer
 * also creates <style> elements and fills them via textContent (theme and
 * cell-size rules). With a strict style-src 'self' those are blocked, so
 * either allow style-src 'unsafe-inline' on /term, or rely on the CSSOM
 * fallback below (re-inserts those rules with insertRule, which CSP allows).
 */
(function () {
  "use strict";
  var root = document.getElementById("term-root");
  if (!root || typeof Terminal === "undefined") return;
  var D = root.dataset;
  var statusEl = document.getElementById("term-status");
  var reconnectBtn = document.getElementById("term-reconnect");
  var bottomBtn = document.getElementById("term-bottom");
  var screen = document.getElementById("term-screen");
  var prevBtn = document.getElementById("term-prev");
  var nextBtn = document.getElementById("term-next");
  var winLabel = document.getElementById("term-winlabel");
  var winCount = 0;
  var histEl = document.getElementById("term-hist");
  var histBar = document.getElementById("term-histbar");
  var histCount = document.getElementById("term-hist-count");
  var histBtn = document.getElementById("term-hist-btn");
  var histRefresh = document.getElementById("term-hist-refresh");
  var liveBtn = document.getElementById("term-live");
  var scrollBox = document.getElementById("term-scroll");
  var histTerm = null, histFit = null, histOn = false, histBusy = false;
  var CLOSE = {
    4401: "Sign in again.", 4403: "Not allowed.", 4408: "Closed: idle timeout.",
    4409: "Closed: time limit reached.", 4413: "Closed: too slow.",
    4429: "Too many terminals open."
  };
  var MIN_RETRY_MS = 5000, MAX_TRIES = 3, HIDDEN_MS = 60000;
  var FONT_KEY = "pm.term.font";

  function loadFont() {
    try { var v = parseInt(localStorage.getItem(FONT_KEY), 10); if (v >= 8 && v <= 28) return v; } catch (e) { /* ignore */ }
    return 13;
  }
  var fontSize = loadFont();

  // Powerline / Nerd Font glyphs (private use area) come from a self-hosted symbols-only font
  // (@font-face in term.css, limited by unicode-range). It is added to the stack only after
  // it has loaded, so xterm measures the cell size once and with the final stack.
  var BASE_FONTS = 'ui-monospace, SFMono-Regular, Menlo, Consolas, "Liberation Mono"';
  var SYMBOLS = '"Symbols Nerd Font Mono"';
  var FONT_BASE = BASE_FONTS + ", monospace";
  var FONT_FULL = BASE_FONTS + ", " + SYMBOLS + ", monospace";
  var fontFamily = FONT_BASE;

  // v4 pane colours: take the resolved background of the pane box.
  var theme = {};
  try {
    var cs = getComputedStyle(document.getElementById("term-scroll"));
    if (cs.backgroundColor) theme = { background: cs.backgroundColor, foreground: cs.color };
  } catch (e) { /* default theme */ }
  var term = new Terminal({
    theme: theme,
    disableStdin: true, cursorBlink: false, scrollback: 5000, fontSize: fontSize,
    fontFamily: fontFamily,
    convertEol: false, allowProposedApi: false
  });
  var fit = new FitAddon.FitAddon();
  term.loadAddon(fit);
  term.open(screen);

  // CSSOM fallback for xterm's <style> elements (see CSP note above).
  try {
    new MutationObserver(function () {
      screen.querySelectorAll("style").forEach(function (el) {
        var text = el.textContent;
        if (!text || el.dataset.cssom || !el.sheet || el.sheet.cssRules.length) return;
        el.dataset.cssom = "1";
        text.split("}").forEach(function (r) {
          if (r.trim()) { try { el.sheet.insertRule(r + "}", el.sheet.cssRules.length); } catch (e) { /* ignore */ } }
        });
      });
    }).observe(screen, { childList: true, subtree: true, characterData: true });
  } catch (e) { /* ignore */ }

  var ws = null, tries = 0, retryTimer = null, hiddenTimer = null;
  var stopped = false, pausedHidden = false, live = false, lastSize = "";

  function setStatus(state, text) {
    statusEl.dataset.state = state;
    statusEl.textContent = text;
    reconnectBtn.hidden = state === "connecting" || state === "live";
  }
  function updateNav() {
    var on = live && winCount > 1;
    prevBtn.disabled = !on || histOn; nextBtn.disabled = !on || histOn;
    histBtn.disabled = !live && !histOn;
  }
  function setWindow(m) {
    var n = parseInt(m.count, 10), p = parseInt(m.pos, 10), i = parseInt(m.index, 10);
    if (!(n >= 1 && p >= 1 && p <= n)) return;
    winCount = n;
    winLabel.textContent = "window " + p + "/" + n + " - " + String(m.name == null ? "" : m.name).slice(0, 64) + " (#" + i + ")";
    updateNav();
    term.scrollToBottom();
  }
  function switchWindow(dir) {
    if (!live || winCount < 2) return;
    sendJSON({ type: "window", dir: dir });
  }
  prevBtn.addEventListener("click", function () { switchWindow("prev"); });
  nextBtn.addEventListener("click", function () { switchWindow("next"); });
  document.addEventListener("keydown", function (e) {
    if (!e.altKey || e.ctrlKey || e.metaKey || e.shiftKey) return;
    var t = e.target && e.target.tagName;
    if (t === "INPUT" || t === "TEXTAREA" || t === "SELECT") return;
    if (e.key === "ArrowLeft") { e.preventDefault(); switchWindow("prev"); }
    else if (e.key === "ArrowRight") { e.preventDefault(); switchWindow("next"); }
    else if (e.key === "h" || e.key === "H") { e.preventDefault(); toggleHistory(); }
  });

  // ---- History view: server-side capture-pane text in a second, read-only xterm.
  function histStatus(t) { histCount.textContent = t; }
  function ensureHist() {
    if (histTerm) return;
    histTerm = new Terminal({
      theme: theme,
      disableStdin: true, cursorBlink: false, scrollback: 10500, fontSize: fontSize,
      fontFamily: fontFamily,
      convertEol: true, allowProposedApi: false
    });
    histFit = new FitAddon.FitAddon();
    histTerm.loadAddon(histFit);
    histTerm.open(histEl);
  }
  function loadHistory() {
    if (histBusy) return;
    histBusy = true; histStatus("Loading history...");
    window.pm.fetch(D.historyPath + "?sid=" + encodeURIComponent(D.sid) + "&n=2000", {
      credentials: "same-origin", cache: "no-store", headers: { "Accept": "application/json" }
    }).then(function (r) {
      if (r.status === 429) throw "Wait a second, then Refresh.";
      if (r.status === 409) throw "Not connected; use Live and Reconnect.";
      if (!r.ok) throw "History unavailable (" + r.status + ").";
      return r.json();
    }).then(function (j) {
      ensureHist();
      try { histFit.fit(); } catch (e) { /* ignore */ }
      histTerm.reset();
      histTerm.options.fontSize = fontSize;
      histTerm.write(String(j.text || ""), function () { histTerm.scrollToBottom(); });
      var w = j.window || {};
      histStatus(j.lines + " lines" + (w.name == null ? "" : " - window " + w.pos + "/" + w.count + " " + String(w.name).slice(0, 32)));
    }).catch(function (e) {
      histStatus(typeof e === "string" ? e : "History failed.");
    }).then(function () { histBusy = false; });
  }
  function showHistory() {
    if (histOn || !live) return;
    histOn = true;
    scrollBox.hidden = true; histEl.hidden = false; histBar.hidden = false;
    histRefresh.hidden = false; histBtn.hidden = true; bottomBtn.hidden = true;
    histBtn.setAttribute("aria-pressed", "true");
    updateNav(); ensureHist(); loadHistory();
  }
  function showLive() {
    if (!histOn) return;
    histOn = false;
    histEl.hidden = true; histBar.hidden = true; scrollBox.hidden = false;
    histRefresh.hidden = true; histBtn.hidden = false;
    histBtn.setAttribute("aria-pressed", "false");
    updateNav(); doFit(); term.scrollToBottom(); onResize();
  }
  function toggleHistory() { if (histOn) showLive(); else showHistory(); }
  histBtn.addEventListener("click", showHistory);
  liveBtn.addEventListener("click", showLive);
  histRefresh.addEventListener("click", loadHistory);
  // Live view has no scrollback (tmux redraws a fixed screen): hint at History.
  var hintT = null;
  function hint() {
    if (histOn || !live || term.buffer.active.baseY > 0) return;
    var old = statusEl.textContent; statusEl.textContent = "Scroll back: tap History (Alt+H)";
    if (hintT) clearTimeout(hintT);
    hintT = setTimeout(function () { hintT = null; if (live) statusEl.textContent = "Live (read-only)"; }, 2500);
    void old;
  }
  scrollBox.addEventListener("wheel", function (e) { if (e.deltaY < 0) hint(); }, { passive: true });
  document.addEventListener("keydown", function (e) { if (e.key === "PageUp" && !e.altKey) hint(); });
  function sendJSON(o) { if (ws && ws.readyState === 1) ws.send(JSON.stringify(o)); }
  function doFit() { try { fit.fit(); } catch (e) { /* ignore */ } }
  function sizeMsg(type) { return { type: type, cols: term.cols, rows: term.rows }; }

  function closeSocket(code) {
    if (retryTimer) { clearTimeout(retryTimer); retryTimer = null; }
    if (ws) { var w = ws; ws = null; w.onclose = null; w.onerror = null; try { w.close(code || 1000); } catch (e) { /* ignore */ } }
    live = false; winCount = 0; if (histOn) showLive(); updateNav();
  }

  function connect() {
    closeSocket();
    stopped = false; pausedHidden = false;
    setStatus("connecting", "Connecting...");
    var body = new URLSearchParams({ sid: D.sid, csrf: D.csrf });
    if (D.window) body.set("w", D.window);
    window.pm.fetch(D.ticketPath, {
      method: "POST", credentials: "same-origin",
      headers: { "Content-Type": "application/x-www-form-urlencoded", "Accept": "application/json" },
      body: body.toString()
    }).then(function (r) {
      if (r.status === 401) { throw { code: 4401 }; }
      if (r.status === 403) { throw { code: 4403 }; }
      if (r.status === 429) { throw { code: 4429 }; }
      if (!r.ok) { throw { msg: "Could not get a ticket (" + r.status + ")." }; }
      return r.json();
    }).then(function (j) {
      if (!j || !j.ticket) throw { msg: "No ticket returned." };
      open(j.ticket);
    }).catch(function (e) {
      if (e && e.code) return finish(e.code);
      scheduleRetry((e && e.msg) || "Network error.");
    });
  }

  function open(ticket) {
    var url = (location.protocol === "https:" ? "wss://" : "ws://") + location.host + D.wsPath + "?ticket=" + encodeURIComponent(ticket);
    var w = new WebSocket(url);
    ws = w;
    w.binaryType = "arraybuffer";
    w.onopen = function () {
      doFit();
      sendJSON(sizeMsg("init"));
      lastSize = term.cols + "x" + term.rows;
      live = true;
      setStatus("live", "Live (read-only)");
      term.reset();
    };
    w.onmessage = function (ev) {
      if (typeof ev.data === "string") {
        var m; try { m = JSON.parse(ev.data); } catch (e) { return; }
        if (m && m.type === "ping") sendJSON({ type: "pong" });
        else if (m && m.type === "window") setWindow(m);
        return;
      }
      term.write(new Uint8Array(ev.data));
    };
    w.onerror = function () { /* close follows */ };
    w.onclose = function (ev) {
      if (ws !== w) return;
      ws = null; var was = live; live = false; if (histOn) showLive(); updateNav();
      finish(ev.code, was);
    };
  }

  function finish(code, wasLive) {
    if (CLOSE[code]) { stopped = true; setStatus("closed", CLOSE[code]); return; }
    if (stopped) return;
    scheduleRetry(code === 1000 ? "Connection closed." : "Connection lost.", wasLive);
  }

  function scheduleRetry(reason) {
    tries += 1;
    if (tries > MAX_TRIES) {
      setStatus("closed", reason + " Gave up after " + MAX_TRIES + " tries.");
      return;
    }
    var wait = Math.max(MIN_RETRY_MS, tries * MIN_RETRY_MS);
    setStatus("closed", reason + " Retrying in " + Math.round(wait / 1000) + " s (" + tries + "/" + MAX_TRIES + ").");
    retryTimer = setTimeout(function () { retryTimer = null; connect(); }, wait);
  }

  function userStop(text) {
    stopped = true; closeSocket(1000);
    setStatus("closed", text);
  }

  // Reconnect button: fresh ticket, resets the retry budget.
  reconnectBtn.addEventListener("click", function () { tries = 0; connect(); });

  // Back, pagehide, hidden > 60 s: close cleanly.
  document.getElementById("term-back").addEventListener("click", function () { stopped = true; closeSocket(1000); });
  window.addEventListener("pagehide", function () { stopped = true; closeSocket(1000); });
  document.addEventListener("visibilitychange", function () {
    if (document.hidden) {
      hiddenTimer = setTimeout(function () {
        hiddenTimer = null; pausedHidden = true;
        userStop("Paused while hidden. Reconnect to resume.");
      }, HIDDEN_MS);
    } else {
      if (hiddenTimer) { clearTimeout(hiddenTimer); hiddenTimer = null; }
      if (pausedHidden) { pausedHidden = false; tries = 0; connect(); }
    }
  });

  // Resize (debounced), orientation change.
  var rt = null;
  function onResize() {
    if (rt) clearTimeout(rt);
    rt = setTimeout(function () {
      rt = null; doFit();
      var s = term.cols + "x" + term.rows;
      if (s !== lastSize) { lastSize = s; sendJSON(sizeMsg("resize")); }
    }, 250);
  }
  try { new ResizeObserver(onResize).observe(document.getElementById("term-scroll")); } catch (e) { /* ignore */ }
  window.addEventListener("orientationchange", onResize);

  // Font size.
  function setFont(n) {
    fontSize = Math.max(8, Math.min(28, n));
    term.options.fontSize = fontSize;
    if (histTerm) histTerm.options.fontSize = fontSize;
    try { localStorage.setItem(FONT_KEY, String(fontSize)); } catch (e) { /* ignore */ }
    onResize();
  }
  document.getElementById("term-font-dec").addEventListener("click", function () { setFont(fontSize - 1); });
  document.getElementById("term-font-inc").addEventListener("click", function () { setFont(fontSize + 1); });

  // Jump to bottom.
  function updateBottom() {
    var b = term.buffer.active;
    bottomBtn.hidden = b.viewportY >= b.baseY;
  }
  term.onScroll(updateBottom);
  term.onWriteParsed(updateBottom);
  bottomBtn.addEventListener("click", function () { term.scrollToBottom(); updateBottom(); });

  // Load the symbols font first (bounded wait), then switch both terminals to the full stack,
  // refit, and only then connect so the first frame is drawn with final cell metrics.
  var started = false;
  function start() {
    if (started) return;
    started = true;
    fontFamily = FONT_FULL;
    term.options.fontFamily = FONT_FULL;
    if (histTerm) histTerm.options.fontFamily = FONT_FULL;
    doFit();
    connect();
  }
  try {
    var wait = new Promise(function (res) { setTimeout(res, 2500); });
    var load = document.fonts.load("14px " + SYMBOLS, String.fromCharCode(0xe0b0, 0xf015, 0xf0e7));
    Promise.race([load, wait]).then(start, start);
  } catch (e) { start(); }

  // Back/forward cache: a restored page has a dead socket; reconnect it.
  window.addEventListener("pageshow", function (e) {
    if (e.persisted && started) { tries = 0; connect(); }
  });
})();
