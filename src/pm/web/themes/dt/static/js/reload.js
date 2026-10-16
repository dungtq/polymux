/* dt theme (pm v7) service reload bar (SHELL.md §4, PARITY "Service reload banner"): a status bar in the .x-lock look
   at the top of #main (div.x-lock.x-rl[role=status]: icon, text, one .x-btn), built here when needed.
   No background polling. /healthz is asked only
   - after this tab's own Reload pm (POST /service/reload answered OK: the account menu's htmx form, the Settings item
     through pm7.fetch, the classic composer's "Restart pm" on pm-project sessions): wait until the old process is
     gone and the new one answers, then reload the page (the user asked for it);
   - after one of this tab's own requests (pm7.fetch or htmx) got 502 / 503 / 504 from nginx: wait until pm answers,
     then offer "Reload now"; it never reloads by itself, so idle or waking tabs are left alone.
   The wait is one setTimeout chain (no setInterval; PARTS.md §13), every /healthz call is aborted after 4 s, and a
   restart wait gives up after 90 s with "Retry". Status 0 (aborted request, sleeping laptop, navigation) is ignored.
   A framed page (the aside's embed) reloads the top page: the top page never sees the frame's requests.
   The 5xx check wraps pm7.fetch once (core.js stays the only fetch( caller); /healthz goes through the unwrapped one.
   Globals: pm7.reload = {wait() (start the restart wait), down() (start the 5xx wait), state() -> '' | mode}. */
(function () {
  'use strict';
  const pm7 = window.pm7, d = document;
  if (!pm7 || !pm7.fetch || pm7.reload) return;
  const MAXWAIT = 90000, GONE = 8000, ASK_MS = 4000;
  const base = pm7.fetch;
  let mode = '', seenDown = false, since = 0, timer = 0, busy = false;

  /* ---------- the bar ---------- */
  function bar() {
    let b = d.getElementById('x-rl');
    if (b) return b;
    const main = d.getElementById('main');
    if (!main) return null;
    b = pm7.el('div', 'x-lock x-rl');
    b.id = 'x-rl';
    b.setAttribute('role', 'status');
    b.setAttribute('aria-live', 'polite');
    const ic = pm7.icon('refresh'), btn = pm7.el('button', 'x-btn');
    btn.type = 'button';
    btn.hidden = true;
    btn.addEventListener('click', () => { if (btn.getAttribute('data-act') === 'retry') wait(); else reloadTop(); });
    if (ic) b.appendChild(ic);
    b.append(pm7.el('span', 'x-lock-t'), btn);
    b.hidden = true;
    main.prepend(b);
    return b;
  }
  function show(text, label, act) {
    const b = bar();
    if (!b) return;
    const t = b.querySelector('.x-lock-t'), btn = b.querySelector('.x-btn');
    t.textContent = text;
    btn.hidden = !label;
    btn.textContent = label || '';
    btn.setAttribute('data-act', act || 'reload');
    b.hidden = false;
    try { b.scrollIntoView({ block: 'nearest' }); } catch (e) { /* old browsers */ }
  }
  function reloadTop() {
    try { (window.top || window).location.reload(); } catch (e) { window.location.reload(); }
  }

  /* ---------- the bounded /healthz wait ---------- */
  function stop() { clearTimeout(timer); timer = 0; mode = ''; seenDown = false; since = 0; }
  function later(ms) { clearTimeout(timer); timer = setTimeout(ping, ms); }
  async function alive() {
    const c = new AbortController(), t = setTimeout(() => c.abort(), ASK_MS);
    try { const r = await base('/healthz', { signal: c.signal }); return r.ok; }
    catch (e) { return false; }
    finally { clearTimeout(t); }
  }
  async function ping() {
    if (busy || !mode) return;
    if (Date.now() - since > MAXWAIT) {
      const m = mode;
      stop();
      if (m === 'restart') show('pm did not come back within 90 s. Check the service: systemctl --user status pm.',
        'Retry', 'retry');
      else show('pm is still not answering. Reload when it is back.', 'Reload now');
      return;
    }
    busy = true;
    const ok = await alive();
    busy = false;
    if (!mode) return;
    if (!ok) { seenDown = true; later(1000); return; }
    if (mode === 'restart') {
      if (!seenDown && Date.now() - since < GONE) { later(700); return; }  // the old process still answers
      stop();
      show('pm is back. Reloading…', '');
      reloadTop();
      return;
    }
    stop();
    show('pm is back. Reload to get the current page.', 'Reload now');
  }
  function wait() {
    stop();
    mode = 'restart';
    since = Date.now();
    show('pm is restarting… this page reloads when it is back.', '');
    later(1000);
  }
  function down() {
    if (mode) return;
    mode = 'down';
    since = Date.now();
    show('pm is not answering (restarting?). Waiting…', 'Reload now');
    later(1500);
  }

  /* ---------- this tab's requests ---------- */
  function seen(path, status, ok) {
    if (/\/service\/reload$/.test(path)) { if (ok) wait(); return true; }
    if (status === 502 || status === 503 || status === 504) down();
    return false;
  }
  pm7.fetch = function (url, opts) {
    return base(url, opts).then((r) => {
      seen(String(url).split('?')[0], r.status, r.ok);
      return r;
    });
  };
  d.addEventListener('htmx:afterRequest', (e) => {
    const t = e.detail || {}, cfg = t.requestConfig || {}, pi = t.pathInfo || {}, x = t.xhr;
    const path = String(cfg.path || pi.requestPath || pi.finalRequestPath || '').split('?')[0];
    if (seen(path, x ? x.status : 0, !!t.successful) && !t.successful && pm7.toast) {
      pm7.toast('Reload pm failed (' + (x && x.status ? 'HTTP ' + x.status : 'no answer') + '). pm was not restarted.',
        '', { error: true });
    }
  });

  pm7.reload = { wait: wait, down: down, state: () => mode };
})();
