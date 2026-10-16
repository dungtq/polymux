/* dt Inbox page (docs/themes/dt/PRD.md §6.2). Markup: templates/_inbox.html, parts/_agentrow.html; parts JS: table.js
   (folds, details), listkeys.js (J K ↵ O X E P, row click / double-click), filter.js, parts.js (⋯ menus, their POSTs
   and toasts), ticker.js (ages). This file adds only what is the Inbox's own:
   - opening: a row click / ↵ (event i:open) opens the chat aside (pm7.aside.open, the list keeps focus; phones: a
     tab), the aside's row is .sel (pm7:aside); a double-click / O (⋯ item "tab",
     event i:tab) opens a tab (pm7.tabs.open); either marks the row read (POST /inbox/read with its key,
     [data-i-rk] / data-i-un) when it was unread.
   - terminal: a[data-i-term] (tmux cell, details) and the ⋯ item "term" open the read-only terminal as a tab.
   - [data-i-run=<item id>] buttons (row details, Resume) run that ⋯ item of their row (pm7.menus.run): one POST,
     confirm and toast path for row and menu.
   - recaps: a row's details load /a/<id>/recap-row into [data-i-recap] on first open; the box is data-keep (the live
     morph never touches it) and cached in memory, so a refresh never flashes; Refresh reloads it in place.
   - live swaps: #inbox is data-nodiff (classic nodiff.js morphs the list): after each morph (nodiff:swapped) the
     user's folds and open details (pm7.table.restore), the aside row, the focused row, key labels and ages are put
     back and the address bar gets the list's URL ([data-poll]); sort / Show all / empty-line links load into #inbox.
   - "Mark all read" (head, [data-i-readall]): POST /inbox/read-all, toast after the server said OK.
   The row hooks (open, tab, terminal, [data-i-run], recaps, the aside's .sel row) also work in any other box of agent
   rows marked [data-i-rows] (the project view's Agents tab, #pj-agents); the live-morph, address-bar and sort / Show
   all loading parts are the Inbox's own (#inbox). */
(function () {
  'use strict';
  const pm7 = window.pm7, d = document;
  const RECAP_TTL = 60000;                 // a cached recap older than this is fetched again on open
  const recaps = new Map();                // agent id -> {html, at}
  const DBL_MS = 800;                      // a double-click's tab follows the row its first click opened
  let selId = null, focusId = null, last = null;

  const BOXES = '#inbox, [data-i-rows]';  // boxes of agent rows: the Inbox, the project view's Agents tab
  const box = () => d.getElementById('inbox');
  const inBox = (el) => !!(el && el.closest && el.closest(BOXES));
  function rowOf(el) {
    if (!el || !el.closest) return null;
    const r = el.closest('[data-row-id]');
    if (r && r.matches('.p-gr')) return r;
    const det = el.closest('.p-det[data-p-det-for]'), t = det && det.closest('.p-g');
    return t ? t.querySelector('.p-gr[data-row-id="' + CSS.escape(det.getAttribute('data-p-det-for')) + '"]') : null;
  }
  function refresh() { if (window.htmx) pm7.$$(BOXES).forEach((b) => window.htmx.trigger(b, 'pm7:refresh')); }

  /* ---------- open: aside, tab, terminal; opening marks read ---------- */
  function markRead(r) {
    const k = r && r.querySelector('[data-i-rk]');
    if (!k || k.getAttribute('data-i-un') !== '1') return;
    k.setAttribute('data-i-un', '0');
    pm7.fetch('/inbox/read', { form: { key: k.getAttribute('data-i-rk') }, keepalive: true })
      .then((res) => { if (res.ok) refresh(); }, () => {});
  }
  function agentHref(id) { return '/a/' + encodeURIComponent(id); }
  function openTab(id) {
    if (pm7.tabs && pm7.tabs.open) pm7.tabs.open('agent', id, agentHref(id));
    else window.location.assign(agentHref(id));
  }
  function showSel() {  // the row shown in the chat aside (pm7:aside events, pm7.aside.current())
    const boxes = pm7.$$(BOXES);
    if (!boxes.length) return;
    selId = pm7.aside.current();
    const on = d.documentElement.classList.contains('aside');
    boxes.forEach((b) => pm7.$$('.p-gr[data-row-id]', b).forEach((r) => {
      r.classList.toggle('sel', on && r.getAttribute('data-row-id') === selId);
    }));
  }
  function openAside(r) {
    const id = r.getAttribute('data-row-id');
    last = { id: id, t: Date.now() };
    markRead(r);
    selId = id;
    pm7.aside.open(id, { focus: false });  // the list keeps the keys (J K); ⌘L puts the cursor in the chat; phones: tab
    showSel();
  }
  function openTerm(a) {
    const sid = a.getAttribute('data-i-term'), href = a.getAttribute('href');
    if (pm7.tabs && pm7.tabs.open && sid) pm7.tabs.open('term', sid, href);
    else window.location.assign(href);
  }
  d.addEventListener('i:open', (e) => { const r = rowOf(e.target); if (r) openAside(r); });
  function tabFor(r) {
    markRead(r);
    openTab(r.getAttribute('data-row-id'));
  }
  d.addEventListener('i:tab', (e) => { const r = rowOf(e.target); if (r) tabFor(r); });
  /* double-click = tab. The first click opened the aside, which reflows the list under the pointer (the controls row
     wraps) or, under 900px, covers it, so the second click may land on another row, a band or the aside: it is
     swallowed, and the tab is the row the first click opened (listkeys.js's own double-click O is skipped below) */
  const recent = () => (last && Date.now() - last.t < DBL_MS ? last.id : null);
  const near = (t) => inBox(t) || !!(t && t.closest && t.closest('#x-aside'));
  window.addEventListener('click', (e) => {
    const id = e.detail > 1 && near(e.target) ? recent() : null, r = id && e.target.closest('.p-gr[data-row-id]');
    if (id && (!r || r.getAttribute('data-row-id') !== id)) { e.preventDefault(); e.stopPropagation(); }
  }, true);
  d.addEventListener('dblclick', (e) => {
    const id = near(e.target) ? recent() : null;
    if (!id && (!inBox(e.target) || e.target.closest('.p-det, a[href], button, input, select'))) return;
    const b = box(), r = id ? b && b.querySelector('.p-gr[data-row-id="' + CSS.escape(id) + '"]') : rowOf(e.target);
    if (r && r.closest('[data-list=inbox]')) tabFor(r);
  });
  d.addEventListener('i:term', (e) => {
    const r = rowOf(e.target), a = r && r.querySelector('a[data-i-term]');
    if (a) openTerm(a);
  });

  /* ---------- recaps: lazy, cached, refreshed in place ---------- */
  function put(c, h) {
    const body = pm7.$('.i-rbody', c), age = pm7.$('[data-i-rage]', c);
    if (body && c.getAttribute('data-i-at') !== String(h.at)) {
      if (body.innerHTML !== h.html) body.innerHTML = h.html;
      c.setAttribute('data-i-at', String(h.at));
    }
    if (age) {
      age.setAttribute('data-since', String(Math.floor(h.at / 1000)));
      age.hidden = false;
      if (pm7.ticker) pm7.ticker.tick();
    }
  }
  function recap(det, force) {
    const c = det && pm7.$('[data-i-recap]', det);
    if (!c) return;
    const id = det.getAttribute('data-p-det-for'), hit = recaps.get(id);
    if (hit) put(c, hit);
    if ((hit && !force && Date.now() - hit.at < RECAP_TTL) || c.hasAttribute('data-i-busy')) return;
    c.setAttribute('data-i-busy', '');
    pm7.fetch(c.getAttribute('data-i-recap'), { headers: { 'HX-Request': 'true' } })
      .then((r) => (r.ok ? r.text() : Promise.reject(new Error(String(r.status)))))
      .then((html) => {
        const h = { html: html, at: Date.now() };
        recaps.set(id, h);
        put(c, h);
      }, () => {
        if (hit) { if (force) pm7.toast('Could not refresh the recap', '', { error: true }); return; }
        const body = pm7.$('.i-rbody', c);
        if (body) body.replaceChildren(Object.assign(d.createElement('p'), { className: 'muted',
          textContent: 'No recap available.' }));
      })
      .finally(() => c.removeAttribute('data-i-busy'));
  }
  d.addEventListener('pm7:det', (e) => {
    const r = e.target;
    if (!inBox(r) || !e.detail || !e.detail.open) return;
    focusId = r.getAttribute('data-row-id');
    const t = r.closest('.p-g');
    recap(t && t.querySelector('.p-det[data-p-det-for="' + CSS.escape(focusId) + '"]'), false);
  });

  /* ---------- clicks ---------- */
  function load(a) {
    const b = box(), href = a.getAttribute('href');
    if (!b || !window.htmx || !href || href[0] !== '/' || href[1] === '/') return false;
    window.htmx.ajax('GET', href, { source: a, target: b, swap: 'innerHTML' });
    return true;
  }
  function readAll(btn) {
    btn.disabled = true;
    pm7.fetch('/inbox/read-all', { form: {} }).then((r) => {
      if (!r.ok) return pm7.fail('Could not mark read', r);
      pm7.toast('Marked everything read');
      refresh();
    }, () => pm7.fail('Could not mark read', null)).finally(() => { btn.disabled = false; });
  }
  d.addEventListener('click', (e) => {
    const t = e.target;
    if (!inBox(t) || e.defaultPrevented || e.button || e.metaKey || e.ctrlKey || e.shiftKey || e.altKey) return;
    const term = t.closest('a[data-i-term]');
    if (term) { e.preventDefault(); openTerm(term); return; }
    const run = t.closest('[data-i-run]');
    if (run) {
      e.preventDefault();
      const r = rowOf(run), m = r && r.querySelector('[data-menu]');
      if (m && pm7.menus) pm7.menus.run(m, run.getAttribute('data-i-run'));
      return;
    }
    const rf = t.closest('[data-i-rfr]');
    if (rf) { e.preventDefault(); recap(rf.closest('.p-det'), true); return; }
    const all = t.closest('[data-i-readall]');
    if (all) { e.preventDefault(); readAll(all); return; }
    const nav = t.closest('.p-gh a.p-sort, .p-foot a.p-more, a[data-i-nav]');
    if (nav && load(nav)) e.preventDefault();
  });

  /* ---------- live swaps (nodiff morph): put the user's state back ---------- */
  d.addEventListener('focusin', (e) => {
    const r = inBox(e.target) ? e.target.closest('.p-gr[data-row-id]') : null;
    if (r) focusId = r.getAttribute('data-row-id');
  });
  function refocus(b) {  // the morph patches rows in place: the Tab stop and focus follow the row id, not the node
    const want = focusId ? b.querySelector('.p-gr[data-row-id="' + CSS.escape(focusId) + '"]') : null;
    if (!want) return;
    pm7.$$('[data-list] .p-gr[data-row-id]', b).forEach((r) => { if (r.closest('.p-g') === want.closest('.p-g')) {
      r.tabIndex = r === want ? 0 : -1; } });
    const a = d.activeElement, cur = a && a.closest ? a.closest('.p-gr[data-row-id]') : null;
    if (cur && b.contains(cur) && cur !== want) want.focus({ preventScroll: true });
  }
  function syncUrl(b) {
    const p = b.querySelector(':scope > [data-poll]'), url = p && p.getAttribute('data-poll');
    if (!url || url[0] !== '/' || url === window.location.pathname + window.location.search) return;
    try { window.history.replaceState(window.history.state, '', url); } catch (err) { /* ignore */ }
  }
  function after() {
    const b = box();
    if (!b) return;
    if (pm7.table) pm7.table.restore(b);
    showSel();
    refocus(b);
    syncUrl(b);
    if (pm7.keys && pm7.keys.fill) pm7.keys.fill(b);
    if (pm7.ticker) pm7.ticker.tick();
    pm7.$$('.p-det:not([hidden])', b).forEach((det) => { if (inBox(det)) recap(det, false); });
  }
  d.addEventListener('pm7:aside', (e) => { selId = e.detail && e.detail.open ? e.detail.id : null; showSel(); });
  d.addEventListener('nodiff:swapped', (e) => { if (e.target && e.target.id === 'inbox') after(); });

  d.addEventListener('htmx:afterSettle', (e) => {  // another box of rows was swapped: its aside row again
    if (e.target && e.target.matches && e.target.matches('[data-i-rows]')) showSel();
  });

  pm7.ready(() => {
    if (pm7.list) pm7.list.on('inbox', { o: (r, id, e) => { if (!e || e.type !== 'dblclick') tabFor(r); } });
    if (box()) after(); else showSel();
  });
})();
