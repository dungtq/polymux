/* Pane tab states (loading / empty / disconnected). External file only (strict CSP).
 * The pane text is rewritten in place by chat.js; here we only mirror it into data-state and
 * watch the poll outcome for data-conn. */
(function () {
  'use strict';
  var LOST_MS = 8000;      // no successful poll for this long => the lost strip
  var lastOk = Date.now(), timer = null, frame = false;

  function wrap() { return document.getElementById('pane'); }
  function setConn(v) { var w = wrap(); if (w && w.getAttribute('data-conn') !== v) w.setAttribute('data-conn', v); }
  function syncState() {
    var w = wrap(), pre = w && w.querySelector('pre.pane');
    if (!pre) return;
    var empty = !pre.textContent.trim();
    var cur = w.getAttribute('data-state');
    if (cur === 'loading' && !frame) return;   // keep Loading until the first frame arrived
    var want = empty ? 'empty' : 'ok';
    if (cur !== want) w.setAttribute('data-state', want);
  }
  function ok() { lastOk = Date.now(); setConn('ok'); }
  function check() {
    if (document.hidden) { lastOk = Date.now(); return; }  // background tabs do not poll
    if (Date.now() - lastOk > LOST_MS) setConn('lost');
  }

  document.addEventListener('htmx:afterRequest', function (e) {
    var el = e.detail && e.detail.elt;
    if (!el || el.id !== 'pane') return;
    if (e.detail.successful) { frame = true; ok(); syncState(); }
    else if (e.detail.xhr && e.detail.xhr.status === 404) setConn('ok');  // agent gone: pm.js shows its note
    else check();
  });
  ['htmx:sendError', 'htmx:timeout', 'htmx:responseError'].forEach(function (n) {
    document.addEventListener(n, function (e) {
      var el = e.detail && e.detail.elt;
      if (el && el.id === 'pane' && Date.now() - lastOk > LOST_MS / 2) setConn('lost');
    });
  });
  document.addEventListener('visibilitychange', function () { if (!document.hidden) lastOk = Date.now(); });
  window.addEventListener('online', function () { var w = wrap(); if (w && window.htmx) window.htmx.trigger(w, 'pm:refresh'); });
  window.addEventListener('offline', function () { setConn('lost'); });

  function init() {
    var w = wrap(), pre = w && w.querySelector('pre.pane');
    if (!pre) return;
    frame = w.getAttribute('data-state') !== 'loading';
    if (window.MutationObserver) new MutationObserver(syncState).observe(pre, { childList: true, characterData: true, subtree: true });
    timer = setInterval(check, 2000);
    syncState();
  }
  if (document.readyState === 'loading') document.addEventListener('DOMContentLoaded', init);
  else init();
})();
