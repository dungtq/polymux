/* dt theme (pm v7) tab strip (SHELL.md §3, §4). Tabs are stored on the server (/dt/tabs*), so every device shows
   the same strip; this file only asks the server and swaps the strip it answers with.
   Globals: pm7.tabs = {open(kind, id, href), close(kind, id), closeCurrent(), reopen(), step(±1), list(), current(),
   refresh(), sheet(anchor)}.
   Hooks: nav#x-tabs[data-rev] > div.x-tab[data-tab-kind][data-tab-id] (home first: .home), each holding
   a[role=tab][href] (current one: .on on the div, aria-selected="true" on the link) and, beside the link, a
   button[data-act=tabclose]; #x-sess a.x-sr[data-sess-id][href] (a click opens the session in a tab; without JS
   it is a plain link); #x-sheet (phone tabs sheet, built here as a.x-st rows).
   Keys on the strip: ←/→ (wrapping), Home, End move focus between the tab links (no navigation; Enter follows the
   link) with a roving tabindex. Classic tabs.js (loaded for un-ported content) also binds these keys on every
   [role=tablist] and would click the tab, so the strip's keydown is handled in the capture phase and stopped.
   Routes: POST /dt/tabs/open|close|reopen|current (form kind, id) and GET /dt/tabs; every call sends
   HX-Current-URL so the server marks the current tab and the home tab for this page. Events: pm7:ui
   ({area: 'tabs', rev} newer than data-rev -> re-fetch), pm7:resync (stream reopened), pm:agent_state (tab dot).
   Hrefs always come from the DOM or the server (X-Dt-Href), never built here. */
(function () {
  'use strict';
  const pm7 = window.pm7, d = document;
  const STATES = ['running', 'waiting', 'failed', 'idle', 'unknown', 'ended'];

  const strip = () => d.getElementById('x-tabs');
  function list() { const s = strip(); return s ? pm7.$$('.x-tab', s) : []; }
  function link(t) { return t.querySelector('a[role=tab]') || t.querySelector('a') || t; }
  function hrefOf(t) { return link(t).getAttribute('href'); }
  function isCur(t) { return t.classList.contains('on') || link(t).getAttribute('aria-selected') === 'true'; }
  function current() { const l = list(); return l.find(isCur) || l.find((t) => t.classList.contains('home')) || null; }
  function kindOf(t) { return t.getAttribute('data-tab-kind') || (t.classList.contains('home') ? 'home' : ''); }
  function idOf(t) { return t.getAttribute('data-tab-id') || ''; }
  function titleOf(t) {
    const n = t.querySelector('.nm') || t.querySelector('span');
    return ((n && n.textContent) || link(t).getAttribute('title') || '').trim();
  }
  function findTab(kind, id) { return list().find((t) => kindOf(t) === kind && idOf(t) === String(id)) || null; }
  function safe(h) { return typeof h === 'string' && h[0] === '/' && h[1] !== '/' && h[1] !== '\\'; }
  function go(href) { if (safe(href)) window.location.assign(href); }

  function send(path, form, opts) {
    const o = opts || {}, ac = new AbortController(), t = setTimeout(() => ac.abort(), o.ms || 6000);
    const req = { headers: { 'HX-Current-URL': window.location.href }, signal: ac.signal };
    if (form) { req.method = 'POST'; req.form = form; }
    if (o.keepalive) req.keepalive = true;
    return pm7.fetch(path, req).finally(() => clearTimeout(t));
  }

  /* swap the strip with a server fragment: the whole nav#x-tabs or only its children */
  function swap(html, rev) {
    const s = strip();
    if (!s || !html) return;
    const doc = new DOMParser().parseFromString(html, 'text/html');
    const nav = doc.getElementById('x-tabs'), src = nav || doc.body;
    const holder = nav && nav.hasAttribute('data-rev') ? nav : pm7.$('[data-rev]', doc.body);
    const newRev = holder ? holder.getAttribute('data-rev') : rev != null ? String(rev) : null;
    s.replaceChildren(...Array.from(src.childNodes).map((n) => d.adoptNode(n)));
    if (newRev != null) s.setAttribute('data-rev', newRev);
    if (window.htmx) window.htmx.process(s);
    rove();
    reveal();
    if (sheetOpen()) sheet(null);
  }
  function answer(r, rev) {
    if (!r.ok) throw new Error(String(r.status));
    if (r.status === 204) return null;
    return r.text().then((h) => { swap(h, rev); return h; });
  }
  function fail(what) { pm7.toast('Could not ' + what + ' the tab', '', { error: true }); }

  /* ---------- verbs ---------- */
  function open(kind, id, href) {  // the page is opened even when the server could not store the tab
    send('/dt/tabs/open', { kind: kind, id: id }).then((r) => r.ok, () => false).then(() => go(href));
  }
  function close(kind, id) {
    const t = findTab(kind, id);
    if (!t || kind === 'home') { pm7.toast('The home tab stays', pm7.keys ? pm7.keys.label('alt+W') : ''); return; }
    const l = list(), i = l.indexOf(t), wasCur = isCur(t), title = titleOf(t);
    const next = l[i + 1] || l[i - 1] || l.find((x) => x.classList.contains('home'));
    const nextHref = next ? hrefOf(next) : '/';
    send('/dt/tabs/close', { kind: kind, id: id }).then((r) => answer(r)).then(() => {
      if (wasCur) go(nextHref || '/');
      else pm7.toast('Closed ' + title);
    }, () => fail('close'));
  }
  function closeCurrent() {
    const c = current();
    if (c) close(kindOf(c), idOf(c));
  }
  function reopen() {
    send('/dt/tabs/reopen', {}).then((r) => {
      if (!r.ok) throw new Error(String(r.status));
      if (r.status === 204) { pm7.toast('No closed tab to reopen'); return; }
      const href = r.headers.get('X-Dt-Href');
      if (safe(href)) { go(href); return; }
      return answer(r);
    }).catch(() => fail('reopen'));
  }
  /* tell the server which tab is current; never blocks or delays the navigation that follows */
  function markCurrent(t) {
    const kind = kindOf(t);
    if (!kind) return;
    send('/dt/tabs/current', { kind: kind, id: idOf(t) }, { keepalive: true }).catch(() => {});
  }
  function step(dir) {
    const l = list(), c = current();
    if (l.length < 2 || !c) { pm7.toast('No other tab'); return; }
    const t = l[(l.indexOf(c) + dir + l.length) % l.length];
    markCurrent(t);
    go(hrefOf(t));
  }

  let refreshing = null;
  function refresh(rev) {
    if (refreshing) return refreshing;
    refreshing = send('/dt/tabs').then((r) => answer(r, rev)).catch(() => {})
      .finally(() => { refreshing = null; });
    return refreshing;
  }

  /* ---------- phone tabs sheet ---------- */
  function sheetOpen() {
    const L = pm7.layers.top();
    return !!L && L.el.id === 'x-sheet' && L.el.hasAttribute('data-tabs');
  }
  function row(t) {
    const a = d.createElement('a'), home = t.classList.contains('home'), cur = isCur(t);
    a.className = 'x-st' + (cur ? ' on' : '');
    a.href = hrefOf(t) || '/';
    if (cur) a.setAttribute('aria-current', 'page');
    if (!home) { a.setAttribute('data-tab-kind', kindOf(t)); a.setAttribute('data-tab-id', idOf(t)); }
    const lead = t.querySelector('.dot, svg');
    if (lead) a.appendChild(lead.cloneNode(true));
    const tx = d.createElement('span'), b = d.createElement('b'), small = d.createElement('small');
    tx.className = 'tx';
    b.textContent = titleOf(t);
    const dot = t.querySelector('.dot');
    const st = dot ? STATES.find((s) => dot.classList.contains(s)) : '';
    small.textContent = home ? 'Home page' : kindOf(t) === 'term' ? 'read-only terminal' : (st || 'session');
    tx.append(b, small);
    a.appendChild(tx);
    if (!home) {
      const x = d.createElement('button'), ic = pm7.icon('x');
      x.type = 'button';
      x.className = 'x-ib';
      x.setAttribute('data-act', 'tabclose');
      x.setAttribute('aria-label', 'Close ' + titleOf(t));
      x.title = 'Close tab';
      if (ic) x.appendChild(ic); else x.textContent = '×';
      a.appendChild(x);
    }
    return a;
  }
  function sheet(anchor) {
    const l = list(), head = d.createElement('div'), n = d.createElement('span'), sp = d.createElement('span');
    const add = d.createElement('button');
    head.className = 'hd';
    n.textContent = l.length + ' open';
    sp.className = 'sp';
    add.type = 'button';
    add.className = 'x-btn sm';
    add.setAttribute('data-act', 'tabnew');
    add.textContent = 'Open session';
    head.append(n, sp, add);
    const host = pm7.sheet(l.map(row), { label: 'Open tabs', head: head, anchor: anchor || null,
      onClose: () => host && host.removeAttribute('data-tabs') });
    if (host) host.setAttribute('data-tabs', '');
  }

  /* keep the current tab in view when the strip overflows */
  function reveal() {
    const s = strip(), c = current();
    if (!s || !c || s.scrollWidth <= s.clientWidth + 1) return;
    const r = c.getBoundingClientRect(), sr = s.getBoundingClientRect();
    if (r.left < sr.left) s.scrollLeft -= sr.left - r.left + 8;
    else if (r.right > sr.right) s.scrollLeft += r.right - sr.right + 8;
  }

  /* ---------- keys on the strip: roving focus, never a click (classic tabs.js must not see these) ---------- */
  const links = () => list().map(link).filter((a) => a.getClientRects().length);
  function rove(to) {  // to = the link that holds the one Tab stop (default: the current tab's)
    const c = to || (current() ? link(current()) : null);
    list().forEach((t) => { const a = link(t); a.tabIndex = a === c ? 0 : -1; });
  }
  d.addEventListener('keydown', (e) => {
    const k = e.key, s = strip();
    if (k !== 'ArrowLeft' && k !== 'ArrowRight' && k !== 'Home' && k !== 'End') return;
    if (!s || !e.target || !e.target.closest || !s.contains(e.target)) return;
    e.stopImmediatePropagation();                     // classic tabs.js (document, bubble) would click the tab
    if (e.altKey || e.ctrlKey || e.metaKey || e.shiftKey) return;
    const l = links(), i = l.indexOf(e.target.closest('a[role=tab]'));
    if (i < 0 || l.length < 2) return;
    const n = k === 'Home' ? l[0] : k === 'End' ? l[l.length - 1]
      : l[(i + (k === 'ArrowRight' ? 1 : l.length - 1)) % l.length];
    e.preventDefault();
    rove(n);
    n.focus();
    if (n.scrollIntoView) n.scrollIntoView({ block: 'nearest', inline: 'nearest' });
  }, true);

  /* ---------- clicks: tab links, sheet rows, sidebar session rows ---------- */
  d.addEventListener('click', (e) => {
    const t = e.target;
    if (!t || !t.closest || e.defaultPrevented || e.button || e.metaKey || e.ctrlKey || e.shiftKey || e.altKey) return;
    if (t.closest('[data-act]')) return;              // the × inside a tab (keys.js dispatch)
    const tab = t.closest('#x-tabs .x-tab, #x-sheet a.x-st');
    if (tab) {                                        // the link itself navigates; this only records it
      const real = tab.classList.contains('x-st') ? findTab(kindOf(tab), idOf(tab)) : tab;
      if (real) markCurrent(real);
      return;
    }
    const sr = t.closest('#x-sess a.x-sr[data-sess-id]');
    if (sr && sr.getAttribute('href')) {
      e.preventDefault();
      open('agent', sr.getAttribute('data-sess-id'), sr.getAttribute('href'));
    }
  });

  d.addEventListener('pm7:ui', (e) => {
    const x = e.detail || {}, s = strip();
    if (x.area !== 'tabs' || !s) return;
    if (Number(x.rev) > Number(s.getAttribute('data-rev') || 0)) refresh(x.rev);
  });
  d.addEventListener('pm7:resync', () => { refresh(); });
  d.addEventListener('pm:agent_state', (e) => {  // a session tab's dot follows its agent between strip swaps
    const x = e.detail || {}, t = x.id ? findTab('agent', x.id) : null, dot = t && t.querySelector('.dot');
    if (!dot || STATES.indexOf(x.state) < 0) return;
    STATES.forEach((s) => dot.classList.remove(s));
    dot.classList.add(x.state);
  });
  window.addEventListener('resize', pm7.debounce(reveal, 150));

  pm7.tabs = { open: open, close: close, closeCurrent: closeCurrent, reopen: reopen, step: step, list: list,
    current: current, refresh: refresh, sheet: sheet };
  pm7.ready(() => { rove(); reveal(); });
})();
