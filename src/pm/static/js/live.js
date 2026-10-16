/* pm live updates: exactly ONE EventSource per tab, owned here (never inside swapped DOM).
 * Pages opt in with a [data-live] element; without one nothing is opened. Server events are
 * re-dispatched on document as `pm:<type>` (pm:agent_state, pm:inbox, ...), which hx-trigger
 * listens to via `from:document`. External file only (strict CSP). */
(function () {
  'use strict';
  if (!window.EventSource) return;
  var URL_ = '/events';
  var HIDDEN_MS = 30000;          // release the stream after the tab was hidden this long
  var TYPES = ['agent_state', 'agent_added', 'agent_removed', 'inbox', 'chat_items', 'chat_outbox'];
  var es = null, retryTimer = null, hideTimer = null, fails = 0, opened = 0, gone = false;

  function wanted() { return !gone && !document.hidden && !!document.querySelector('[data-live]'); }
  function fire(name, detail) { document.dispatchEvent(new CustomEvent(name, { detail: detail || null })); }
  function refreshAll() {
    if (!window.htmx) return;
    document.querySelectorAll('#inbox, #chat, #pane, #proj-live').forEach(function (el) { window.htmx.trigger(el, 'pm:refresh'); });
  }
  function clearRetry() { if (retryTimer) { clearTimeout(retryTimer); retryTimer = null; } }
  function close() {
    clearRetry();
    if (es) { es.onopen = es.onerror = null; try { es.close(); } catch (e) { /* ignore */ } es = null; }
  }
  function later(ms) {
    clearRetry();
    retryTimer = setTimeout(function () { retryTimer = null; open(); }, ms);
  }
  function backoff() {  // 3 s .. 30 s, jittered
    var base = Math.min(30, 3 * Math.pow(2, fails));
    return Math.max(3, Math.min(30, base * (0.75 + Math.random() * 0.5))) * 1000;
  }
  function open() {
    if (es || !wanted()) return;
    var s = new EventSource(URL_, { withCredentials: true });
    es = s;
    s.onopen = function () {
      fails = 0; opened++;
      fire('pm:live-open');
      if (opened > 1) refreshAll();  // catch up on what was missed while closed
    };
    s.onerror = function () {      // never rely on the native auto-reconnect: it has no backoff
      if (es !== s) return;
      close(); fails++;
      fire('pm:live-error');
      later(backoff());
    };
    TYPES.forEach(function (t) {
      s.addEventListener(t, function (e) {
        var d = null; try { d = JSON.parse(e.data); } catch (x) { /* ignore */ }
        fire('pm:' + t, d);
      });
    });
    s.addEventListener('reset', function () { refreshAll(); });
    s.addEventListener('recycle', function () {  // server ended the stream cleanly: reopen at once
      if (es !== s) return;
      close(); later(200 + Math.random() * 800);
    });
    s.addEventListener('bye', function () {      // another tab of this browser took our slot
      if (es !== s) return;
      close(); fire('pm:live-error');
      later(20000 + Math.random() * 20000);      // pages keep polling meanwhile (pm.js / every 30s)
    });
  }

  document.addEventListener('visibilitychange', function () {
    if (document.hidden) {
      if (!hideTimer) hideTimer = setTimeout(function () { hideTimer = null; close(); }, HIDDEN_MS);
      return;
    }
    if (hideTimer) { clearTimeout(hideTimer); hideTimer = null; }
    if (!es) { clearRetry(); fails = 0; open(); refreshAll(); }
  });
  window.addEventListener('pagehide', function () { gone = true; close(); });
  window.addEventListener('pageshow', function (e) {
    if (!e.persisted) return;     // restored from bfcache: the old stream was closed on pagehide
    gone = false; fails = 0; open(); refreshAll();
  });
  open();
})();
