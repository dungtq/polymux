/* dt theme (pm v7) live updates: the page's ONLY EventSource('/events'), owned here (never inside swapped DOM).
   Opened when the page has body[data-live] (the dt shell always has it). Classic stream types (agent_state,
   agent_added, agent_removed, inbox, chat_items, chat_outbox, pm_code) are re-dispatched on document as `pm:<type>`
   with the parsed JSON as detail, so hx-trigger="pm:<type> from:document" in shell and un-ported classic content
   works (pm:pm_code is the "new code" state of js/reload.js); the `ui` type becomes `pm7:ui` ({area, rev}).
   Also fired: pm:live-open ({again}), pm:live-error, pm7:resync (after a reopen or a server `reset`: the shell
   re-reads its tabs and session list).
   Ported from classic live.js: no native auto-reconnect (it has no backoff): 3-30 s jittered backoff, `recycle`
   reopens at once, `bye` (another tab took the slot) waits 20-40 s, the stream is released 30 s after the tab was
   hidden and on pagehide, reopened on show / bfcache restore. A reopened EventSource cannot send Last-Event-ID (the
   header is only sent by the browser's own reconnect), so, as in classic, every reopen re-fetches #inbox, #chat,
   #pane and #proj-live (htmx `pm:refresh`) to catch up on what was missed. */
(function () {
  'use strict';
  const pm7 = window.pm7, d = document;
  if (!window.EventSource) return;
  const URL_ = '/events';
  const HIDDEN_MS = 30000;
  const TYPES = ['agent_state', 'agent_added', 'agent_removed', 'inbox', 'chat_items', 'chat_outbox', 'pm_code'];
  const REFRESH = '#inbox, #chat, #pane, #proj-live';
  let es = null, retryTimer = 0, hideTimer = 0, fails = 0, opened = 0, gone = false;

  function wanted() { return !gone && !d.hidden && !!d.querySelector('body[data-live], [data-live]'); }
  function parse(e) { try { return JSON.parse(e.data); } catch (x) { return null; } }
  function refreshAll() {
    if (window.htmx) d.querySelectorAll(REFRESH).forEach((el) => window.htmx.trigger(el, 'pm:refresh'));
    pm7.emit('pm7:resync');
  }
  function clearRetry() { if (retryTimer) { clearTimeout(retryTimer); retryTimer = 0; } }
  function close() {
    clearRetry();
    if (!es) return;
    es.onopen = es.onerror = null;
    try { es.close(); } catch (x) { /* already closed */ }
    es = null;
  }
  function later(ms) {
    clearRetry();
    retryTimer = setTimeout(() => { retryTimer = 0; open(); }, ms);
  }
  function backoff() {  // 3 s .. 30 s, jittered
    const base = Math.min(30, 3 * Math.pow(2, fails));
    return Math.max(3, Math.min(30, base * (0.75 + Math.random() * 0.5))) * 1000;
  }
  function open() {
    if (es || !wanted()) return;
    const s = new EventSource(URL_, { withCredentials: true });
    es = s;
    s.onopen = () => {
      fails = 0;
      opened++;
      pm7.emit('pm:live-open', { again: opened > 1 });
      if (opened > 1) refreshAll();  // catch up on what was missed while closed
    };
    s.onerror = () => {
      if (es !== s) return;
      close();
      fails++;
      pm7.emit('pm:live-error');
      later(backoff());
    };
    TYPES.forEach((t) => s.addEventListener(t, (e) => pm7.emit('pm:' + t, parse(e))));
    s.addEventListener('ui', (e) => pm7.emit('pm7:ui', parse(e)));
    s.addEventListener('reset', () => refreshAll());  // the server restarted: our ids mean nothing there
    s.addEventListener('recycle', () => {             // the server ended the stream cleanly: reopen at once
      if (es !== s) return;
      close();
      later(200 + Math.random() * 800);
    });
    s.addEventListener('bye', () => {                 // another tab of this browser took our slot
      if (es !== s) return;
      close();
      pm7.emit('pm:live-error');
      later(20000 + Math.random() * 20000);
    });
  }

  d.addEventListener('visibilitychange', () => {
    if (d.hidden) {
      if (!hideTimer) hideTimer = setTimeout(() => { hideTimer = 0; close(); }, HIDDEN_MS);
      return;
    }
    if (hideTimer) { clearTimeout(hideTimer); hideTimer = 0; }
    if (!es) { clearRetry(); fails = 0; open(); refreshAll(); }
  });
  window.addEventListener('pagehide', () => { gone = true; close(); });
  window.addEventListener('pageshow', (e) => {
    if (!e.persisted) return;  // restored from bfcache: the old stream was closed on pagehide
    gone = false;
    fails = 0;
    open();
    refreshAll();
  });
  pm7.ready(open);
})();
