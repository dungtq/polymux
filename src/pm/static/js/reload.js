/* Service-restart banner at the top of every page. No background polling: a tab only checks /healthz
 * - after its own topbar Reload (then it waits for pm and reloads itself, the user asked for it), or
 * - after one of its own htmx requests got 502/503/504 from nginx (then it waits until pm answers and
 *   offers "Reload now"; it never reloads by itself, so idle or waking tabs are left alone).
 * Status 0 (aborted request, sleeping laptop, navigation) is ignored.
 * "New code" bar #pmcode (base.html; shown at render from pm_code()): pm's code is newer than the process. Shown
 * live on the pm_code event (live.js); its Restart pm form is a guarded htmx POST, then the restart wait below. */
(function () {
  'use strict';
  var bar = document.getElementById('reloadbar');
  if (!bar) return;
  // Framed pages (chat drawer, embed) own their own bar and reload the TOP page when pm is back:
  // the top page never sees the frame's requests, so it cannot do it for them.
  // The composer notice row ([data-cmp-reload], agent pages) takes all states when present; the page-level
  // bar is only for pages without it.
  var row = document.querySelector('[data-cmp-reload]');
  var txt = bar.querySelector('.rb-t'), btn = bar.querySelector('.rb-b');
  if (row) { txt = row.querySelector('.pmr-t'); btn = row.querySelector('.pmr-act'); bar = row; }
  var MAXWAIT = 90000;
  var mode = '', seenDown = false, since = 0, timer = 0, busy = false;
  var code = document.getElementById('pmcode');

  function reloadTop() { try { (window.top || window).location.reload(); } catch (e) { location.reload(); } }
  function show(msg, label, keep) {
    if (row) { if (keep) row.removeAttribute('data-lock'); else row.setAttribute('data-lock', '1'); }
    txt.textContent = msg; txt.title = msg;
    btn.hidden = !label; if (label) btn.textContent = label;
    bar.hidden = false;
    try { bar.scrollIntoView({ block: 'nearest' }); } catch (e) { /* ignore */ }
  }
  function stop() { clearTimeout(timer); mode = ''; seenDown = false; since = 0; }
  function ping() {
    if (busy || !mode) return;
    if (mode === 'restart' && Date.now() - since > MAXWAIT) {
      stop(); show('pm did not come back within ' + (MAXWAIT / 1000) + ' s. Check the service (systemctl --user status pm).', 'Retry');
      btn.setAttribute('data-act', 'retry'); return;
    }
    busy = true;
    var c = new AbortController(), t = setTimeout(function () { c.abort(); }, 4000);
    window.pm.fetch('/healthz', { cache: 'no-store', credentials: 'same-origin', signal: c.signal })
      .then(function (r) { return r.ok; }, function () { return false; })
      .then(function (ok) {
        clearTimeout(t); busy = false;
        if (!mode) return;
        if (!ok) { seenDown = true; schedule(1000); return; }
        if (mode === 'restart') {
          // restart requested but the old process still answers: wait for it to go down (or 8 s)
          if (!seenDown && Date.now() - since < 8000) { schedule(700); return; }
          stop(); show('pm is back. Reloading...', ''); reloadTop(); return;
        }
        stop(); show('pm is back. Reload to get the current page.', 'Reload now');
        btn.setAttribute('data-act', 'reload');
      });
  }
  function schedule(ms) { clearTimeout(timer); timer = setTimeout(ping, ms); }
  function begin() {
    mode = 'restart'; seenDown = false; since = Date.now();
    if (code) code.hidden = true;
    btn.setAttribute('data-act', 'reload');
    show('Restarting pm... this page reloads when it is back.', ''); schedule(1000);
  }
  window.pmReloadWait = begin;  // used by tests

  btn.addEventListener('click', function () {
    if (btn.getAttribute('data-act') === 'retry') { begin(); return; }
    reloadTop();
  });
  document.addEventListener('pm:pm_code', function (e) {
    var j = e.detail || {};
    if (!code || mode || j.need !== 'restart') return;
    if (j.msg) code.querySelector('.pc-t').textContent = j.msg;
    code.querySelector('.pc-h').textContent = j.head || '';
    code.hidden = false;
  });
  document.addEventListener('htmx:afterRequest', function (e) {
    var d = e.detail || {}, cfg = d.requestConfig || {}, pi = d.pathInfo || {}, x = d.xhr;
    var path = (cfg.path || pi.requestPath || pi.finalRequestPath || '').split('?')[0];
    if (/\/service\/reload$/.test(path)) {
      if (d.successful) { begin(); return; }
      show('Restart request failed (' + (x && x.status ? 'HTTP ' + x.status : 'no answer') + '). pm was not restarted.', '', true);
      return;
    }
    if (mode || !x || !(x.status === 502 || x.status === 503 || x.status === 504)) return;
    mode = 'down'; show('pm is not answering (restarting?). Waiting...', ''); schedule(1500);
  });
})();
