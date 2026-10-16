/* dt theme (pm v7) Notifications page and the top-bar bell (PRD.md §6.7, DESIGN.md §6.8-6.14, §8).
   Loaded by base.html on every page with the top bar (not embed: no bell); the page part runs only where .n-pg is.
   Bell (button.x-bell[data-n-bell]): a click asks GET /ntfy/panel?scope=all (the list, templates/_ntfy.html; the
     core's /ntfy/bell is system-only) and shows it with pm7.menu (#x-menu gets .n-bell while open): Mark all read
     (POST /inbox/read-all), the newest six messages of every source, unread first (a row marks itself read and opens
     its link or /ntfy?open=<key>), All notifications. The badge (.b on the bell and on the bottom nav's Alerts) =
     the unread system count: from the bell's list, else GET /inbox/unread ("system") at load and after the inbox
     stream event (pm:inbox, debounced).
   Page (.n-pg[data-n-src][data-n-per][data-n-un], templates/ntfy.html; list templates/_ntfy.html in #n-panel):
     Source segments, Unread only switch and period select change .n-pg's attributes (css/notifs.css hides the rows)
     and the URL (replaceState); the user's choice is saved (pm.v7.notifs.src / .un / .per: one view for /ntfy and
     /notifications) and restored on a URL without src / un / per; /notifications keeps Source System for that load.
     Counts (segments, bands, stats line) are recounted here with the same rule as the CSS (ok() below), also after
     every swap of #n-panel and every filter-box change; the head's Mark all read hides while nothing is unread. A
     band whose rows are all out of view hides (.n-off). Rows: a click toggles the details (pm7:det open marks an
     unread row read); ↵ opens the chat in the aside (pm7.aside, else the agent page; no agent: details), O opens
     it in a tab (pm7.tabs), E / P run the row's ⋯ items "read" / "pin" (listkeys data-key-*); the details buttons
     [data-n-act] run the same ⋯ items; Recap loads /a/<id>/recap-row into .n-rx (kept across swaps); a band's Mark
     read POSTs /inbox/read per unread row shown; the head's Mark all read POSTs /inbox/read-all (/inbox/read-system
     under Source System).
     A live reload of the list (the stream's inbox event) waits while a menu or dialog is open or a control in the
     list has focus (a swap would close the menu / drop the focus) and is retried once that is over; ?open=<key>
     opens that row once. Every POST goes through pm7.fetch (CSRF); toasts only after the server answered. */
(function () {
  'use strict';
  const pm7 = window.pm7, d = document;
  if (!pm7 || pm7.notifs) return;
  const ROW = '.n-row[data-row-id]';
  const MENU = '[data-menu]';

  /* ---------- badge: the unread system count on the bell and the bottom nav's Alerts ---------- */
  function badge(host, n, label) {
    if (!host) return;
    let b = host.querySelector('.b');
    if (!n) { if (b) b.remove(); return; }
    if (!b) { b = d.createElement('span'); b.className = 'b'; host.appendChild(b); }
    b.textContent = String(n);
    if (label) b.setAttribute('aria-label', n + ' unread'); else b.setAttribute('aria-hidden', 'true');
  }
  function setBadge(n) {
    n = Math.max(0, parseInt(n, 10) || 0);
    const bell = d.querySelector('[data-n-bell]');
    if (bell) {
      badge(bell, n, false);
      bell.setAttribute('aria-label', 'Notifications' + (n ? ', ' + n + ' unread' : ''));
    }
    badge(d.querySelector('.x-bnav a[data-nav=notifications]'), n, true);
  }
  async function loadBadge() {
    if (!d.querySelector('[data-n-bell], .x-bnav a[data-nav=notifications]')) return;
    try {
      const r = await pm7.fetch('/inbox/unread');
      if (r.ok) setBadge((await r.json()).system);
    } catch (e) { /* the badge stays as it was */ }
  }

  /* ---------- the bell popover: the newest six messages of every source, unread first ---------- */
  const BELL_N = 6;
  function bellRows(html) {
    const t = d.createElement('template');
    t.innerHTML = html;  // our own server's fragment (_ntfy.html)
    const box = t.content.querySelector('.n-in');
    if (!box) return null;
    const rows = pm7.$$(ROW, box).filter((r) => !r.closest('[data-p-grp=pinned]')).map((r) => {
      const key = r.getAttribute('data-row-id'), nm = r.querySelector('.gc-nm .nm'), end = r.querySelector('.gc-end');
      return { key: key, title: nm ? nm.textContent.trim() : '', un: r.classList.contains('un'),
        sys: r.classList.contains('n-system'), pri: parseInt(r.getAttribute('data-n-pri'), 10) || 3,
        href: r.getAttribute('data-n-click') || '/ntfy?open=' + encodeURIComponent(key),
        ago: end ? end.textContent.trim() : '', abs: r.getAttribute('data-n-abs') || '' };
    });
    const un = rows.filter((x) => x.un);
    return { unread: un.length, system: un.filter((x) => x.sys).length,
      rows: un.concat(rows.filter((x) => !x.un)).slice(0, BELL_N) };
  }
  /* POST with the CSRF token; the Response, or null when there was no answer */
  async function post(url, form, keepalive) {
    try { return await pm7.fetch(url, { form: form || {}, keepalive: !!keepalive }); } catch (e) { return null; }
  }
  const fail = (what, r) => pm7.fail(what, r);  // the server's message, else the status (core.js)
  function readKey(key, keepalive) {
    return post('/inbox/read', { key: key }, keepalive).then((r) => !!(r && r.ok));
  }
  async function readAllBell() {
    const r = await post('/inbox/read-all');
    if (!r || !r.ok) { fail('Could not mark read', r); return; }
    pm7.toast('All marked read');
    setBadge(0);
    refreshList();
  }
  async function openBell(btn) {
    if (btn.getAttribute('aria-expanded') === 'true') { pm7.menu(btn, []); return; }  // same anchor again = close
    let r = null;
    try { r = await pm7.fetch('/ntfy/panel?scope=all'); } catch (e) { r = null; }
    const data = r && r.ok ? bellRows(await r.text()) : null;
    if (!data) { fail('Notifications are not available', r); return; }
    setBadge(data.system);
    const L = pm7.keys ? pm7.keys.label : (k) => k;
    const items = [{ label: 'Mark all read', icon: 'check', disabled: !data.unread, run: readAllBell },
      { sep: true }];
    data.rows.forEach((x) => items.push({
      label: x.title, note: (x.pri !== 3 ? 'P' + x.pri + ' · ' : '') + x.ago + (x.un ? ' · unread' : ''),
      href: x.href, run: () => { if (x.un) readKey(x.key, true); }
    }));
    if (!data.rows.length) items.push({ label: 'No notifications yet', disabled: true });
    items.push({ sep: true }, { label: 'All notifications', icon: 'bell', href: '/ntfy', kbd: L('G N') });
    const host = d.getElementById('x-menu');
    if (host) host.classList.add('n-bell');
    const m = pm7.menu(btn, items, { label: 'Notifications', onClose: () => host && host.classList.remove('n-bell') });
    if (!m) { if (host) host.classList.remove('n-bell'); return; }
    const nodes = pm7.$$('[role=menuitem]', m).filter((n) => n.querySelector('small'));
    data.rows.forEach((x, i) => {
      const n = nodes[i];
      if (!n) return;
      n.title = x.title + (x.abs ? ' (' + x.abs + ')' : '');
      if (x.un) n.classList.add('un');
    });
  }

  /* ---------- page: state, the row rule, counts ---------- */
  const page = () => d.querySelector('.n-pg');
  const list = () => d.getElementById('n-list');
  const panel = () => d.getElementById('n-panel');
  function state(p) {
    return { src: p.getAttribute('data-n-src') || 'all', un: p.hasAttribute('data-n-un'),
      per: p.getAttribute('data-n-per') || 'all' };
  }
  /* the row is in this view (the same rule as css/notifs.css); the filter box's own [hidden] is separate */
  function ok(r, st) {
    const c = r.classList, pinBand = !!r.closest('[data-p-grp=pinned]');
    if (st.src === 'pinned') { if (!pinBand) return false; }
    else if (pinBand) { if (st.src !== 'all' || st.un) return false; }
    else if (st.src !== 'all' && !c.contains('n-' + st.src)) return false;
    if (st.un && !c.contains('un')) return false;
    return !(st.per !== 'all' && c.contains('n-pp') && !c.contains('n-p-' + st.per));
  }
  function setText(el, t) { if (el && el.textContent !== t) el.textContent = t; }
  function recount() {
    const p = page(), l = list();
    if (!p || !l) return;
    const st = state(p), rows = pm7.$$(ROW, l);
    let shown = 0;
    pm7.$$('.p-gg', l).forEach((g) => {
      let n = 0, u = 0;
      pm7.$$(ROW, g).forEach((r) => { if (ok(r, st) && !r.hidden) { n++; if (r.classList.contains('un')) u++; } });
      shown += n;
      g.classList.toggle('n-off', !n);
      const f = g.querySelector('.p-fold');
      if (!f) return;
      setText(f.querySelector('.c'), String(n));
      let note = f.querySelector('.note');
      if (g.getAttribute('data-p-grp') !== 'pinned') {
        if (!note && u) { note = d.createElement('span'); note.className = 'note'; f.appendChild(note); }
        if (note) setText(note, u ? '· ' + u + ' unread' : '');
        const b = g.querySelector('[data-n-dayread]');
        if (b) b.hidden = !u;
      }
    });
    const fnone = l.querySelector('[data-p-fnone]');
    p.toggleAttribute('data-n-none', !shown && !!rows.length && !(fnone && !fnone.hidden));
    const segs = { all: 0, agent: 0, system: 0, pinned: 0 };   // All = the history (a pin is also in its day)
    rows.forEach((r) => Object.keys(segs).forEach((k) => {
      if (ok(r, { src: k, un: st.un, per: st.per }) && (k !== 'all' || !r.closest('[data-p-grp=pinned]'))) segs[k]++;
    }));
    pm7.$$('.p-seg a[data-p-seg]', p).forEach((a) => {
      setText(a.querySelector('.c'), String(segs[a.getAttribute('data-p-seg')] || 0));
    });
    const day = rows.filter((r) => !r.closest('[data-p-grp=pinned]'));
    const un = day.filter((r) => r.classList.contains('un')).length;
    const sysUn = day.filter((r) => r.classList.contains('un') && r.classList.contains('n-system')).length;
    const all = p.querySelector('[data-n-readall]');  // what it marks: every message, or the system ones
    if (all) all.hidden = !(st.src === 'system' ? sysUn : un);
    const today = pm7.$$(ROW, l.querySelector('[data-p-grp=today]') || d.createElement('i')).length;
    const parts = [day.length + ' message' + (day.length === 1 ? '' : 's'), un + ' unread', today + ' today'];
    if (shown !== day.length) parts.push(shown + ' shown');
    setText(d.getElementById('n-head-stats'), parts.join(' · '));
  }

  /* ---------- page: controls ---------- */
  function syncUrl(p) {
    const st = state(p), u = new URL(window.location.href), q = pm7.filter ? pm7.filter.q('notifs').trim() : '';
    const set = (k, v, dflt) => { if (v && v !== dflt) u.searchParams.set(k, v); else u.searchParams.delete(k); };
    set('src', st.src, p.getAttribute('data-n-dsrc'));
    set('per', st.per, p.getAttribute('data-n-dper'));
    set('un', st.un ? '1' : '', '');
    set('q', q, '');
    u.searchParams.delete('open');
    window.history.replaceState(window.history.state, '', u.pathname + u.search + u.hash);
  }
  /* the saved view: one for /ntfy and /notifications; a URL with its own src / un / per wins, and /notifications
     keeps Source System for that load (not saved) */
  const SAVED = ['src', 'un', 'per'];
  function save(change) {
    SAVED.forEach((k) => {
      if (change[k] !== undefined) pm7.store.set('notifs.' + k, k === 'un' ? (change.un ? '1' : '0') : change[k]);
    });
  }
  function restore(p) {
    const u = new URL(window.location.href), ch = {};
    if (SAVED.some((k) => u.searchParams.has(k))) return;
    const src = pm7.store.get('notifs.src', ''), per = pm7.store.get('notifs.per', ''), un = pm7.store.get('notifs.un');
    const seg = src && p.querySelector('a[data-p-seg="' + CSS.escape(src) + '"]');
    if (seg && p.getAttribute('data-n-dsrc') !== 'system') ch.src = src;
    if (per && p.querySelector('select[name=per] option[value="' + CSS.escape(per) + '"]')) ch.per = per;
    if (un !== null) ch.un = un === '1';
    if (Object.keys(ch).length) apply(p, ch);
  }
  function apply(p, change, user) {
    if (user) save(change);
    if (change.src) {
      p.setAttribute('data-n-src', change.src);
      pm7.$$('.p-seg a[data-p-seg]', p).forEach((a) => {
        if (a.getAttribute('data-p-seg') === change.src) a.setAttribute('aria-current', 'true');
        else a.removeAttribute('aria-current');
      });
    }
    if (change.per) {
      p.setAttribute('data-n-per', change.per);
      const s = p.querySelector('select[name=per]');
      if (s && s.value !== change.per) s.value = change.per;
    }
    if (change.un !== undefined) {
      p.toggleAttribute('data-n-un', change.un);
      const sw = p.querySelector('button.sw[data-p-sw=un]'), i = p.querySelector('input[name=un]');
      if (sw) sw.setAttribute('aria-checked', change.un ? 'true' : 'false');
      if (i) i.value = change.un ? '1' : '0';
    }
    recount();
    syncUrl(p);
  }
  function refreshList() {
    const pn = panel();
    if (pn && window.htmx) window.htmx.trigger(pn, 'pm7:refresh');
  }

  /* ---------- page: rows ---------- */
  function agentOf(r) {
    const a = r && r.querySelector('[data-n-agent]');
    return a ? a.getAttribute('data-n-agent') : '';
  }
  const rowOf = (el) => (el && el.closest ? el.closest(ROW) : null);
  function chat(id) {  // the chat aside (phones: its tab, aside.js)
    if (!id) return false;
    if (!pm7.aside.open(id)) window.location.assign('/a/' + encodeURIComponent(id));
    return true;
  }
  function tab(id) {
    if (!id) return false;
    const href = '/a/' + encodeURIComponent(id);
    if (pm7.tabs && pm7.tabs.open) pm7.tabs.open('agent', id, href); else window.location.assign(href);
    return true;
  }
  function runItem(r, id) {
    const m = r && r.querySelector(MENU);
    return !!(m && pm7.menus && pm7.menus.run(m, id));
  }
  async function markRead(r) {
    if (!r || !r.classList.contains('un') || r.closest('[data-p-grp=pinned]')) return;
    if (await readKey(r.getAttribute('data-row-id'))) refreshList();
  }
  async function readMany(rows, what) {
    const keys = rows.map((r) => r.getAttribute('data-row-id'));
    if (!keys.length) return;
    let okN = 0;
    for (const k of keys) { if (await readKey(k)) okN++; }
    if (okN === keys.length) pm7.toast(what + ' marked read');
    else pm7.toast('Marked ' + okN + ' of ' + keys.length + ' read', '', { error: true });
    refreshList();
  }
  async function readAll(p) {
    const url = state(p).src === 'system' ? '/inbox/read-system' : '/inbox/read-all';
    const r = await post(url);
    if (!r || !r.ok) { fail('Could not mark read', r); return; }
    pm7.toast(state(p).src === 'system' ? 'System messages marked read' : 'All marked read');
    refreshList();
    loadBadge();
  }

  /* recap boxes: loaded once per row key, put back after a swap */
  const recaps = new Map();   // row key -> {html, open}
  function recapBtn(r, open) {
    const det = r && d.querySelector('.p-det[data-p-det-for="' + CSS.escape(r.getAttribute('data-row-id')) + '"]');
    const b = det && det.querySelector('[data-n-act=recap]');
    if (!b) return;
    b.setAttribute('aria-expanded', open ? 'true' : 'false');
    setText(b.querySelector('span'), open ? 'Hide recap' : 'Recap');
  }
  function recap(r) {
    const key = r.getAttribute('data-row-id');
    const det = d.querySelector('.p-det[data-p-det-for="' + CSS.escape(key) + '"]');
    const box = det && det.querySelector('[data-n-rx]');
    if (!box) return;
    const m = recaps.get(key) || { html: null, open: false };
    m.open = box.hidden;
    recaps.set(key, m);
    box.hidden = !m.open;
    recapBtn(r, m.open);
    if (!m.open || m.html !== null) return;
    box.textContent = 'Loading the recap…';
    pm7.fetch(box.getAttribute('data-n-rx')).then((x) => (x.ok ? x.text() : Promise.reject(x.status)))
      .then((h) => { m.html = h; const b = cur(key); if (b) b.innerHTML = h; })   // our own server's fragment
      .catch(() => { const b = cur(key); if (b) b.textContent = 'No recap available.'; });
  }
  function cur(key) {
    const det = d.querySelector('.p-det[data-p-det-for="' + CSS.escape(key) + '"]');
    return det ? det.querySelector('[data-n-rx]') : null;
  }
  function restoreRecaps() {
    recaps.forEach((m, key) => {
      const box = cur(key);
      if (!box || !m.open) return;
      if (m.html !== null) box.innerHTML = m.html;
      box.hidden = false;
      recapBtn(d.querySelector(ROW + '[data-row-id="' + CSS.escape(key) + '"]'), true);
    });
  }

  /* ?open=<key>: that row in view, its details open, focused (the bell's links); once */
  function openFromUrl(p) {
    const u = new URL(window.location.href), key = u.searchParams.get('open');
    if (!key) return;
    const r = pm7.$$(ROW, list() || d).find((x) => x.getAttribute('data-row-id') === key &&
      !x.closest('[data-p-grp=pinned]'));
    if (r) {
      if (!ok(r, state(p))) apply(p, { src: 'all', per: 'all', un: false });
      const g = r.closest('.p-gg');
      if (g && g.classList.contains('fold') && pm7.table) pm7.table.fold(g, true);
      if (pm7.table) pm7.table.det(r, true);
      r.focus({ preventScroll: true });
      r.scrollIntoView({ block: 'center' });
    }
    syncUrl(p);
  }

  /* ---------- events ---------- */
  d.addEventListener('click', (e) => {
    const t = e.target;
    if (!t || !t.closest || e.defaultPrevented) return;
    const bell = t.closest('[data-n-bell]');
    if (bell) { e.preventDefault(); openBell(bell); return; }
    const p = page();
    if (!p || !p.contains(t)) return;
    const plain = !e.button && !e.metaKey && !e.ctrlKey && !e.shiftKey && !e.altKey;
    const seg = t.closest('.p-seg a[data-p-seg]');
    if (seg && plain) { e.preventDefault(); apply(p, { src: seg.getAttribute('data-p-seg') }, true); return; }
    if (t.closest('[data-n-readall]')) { e.preventDefault(); readAll(p); return; }
    if (t.closest('[data-n-reset]') && plain) {
      e.preventDefault();
      const f = p.querySelector('input[data-p-filter=notifs]');
      if (f && f.value && pm7.filter) pm7.filter.clear(f);
      apply(p, { src: 'all', per: 'all', un: false }, true);
      return;
    }
    const day = t.closest('[data-n-dayread]');
    if (day) {
      e.preventDefault();
      const g = day.closest('.p-gg'), st = state(p);
      const rows = g ? pm7.$$(ROW, g).filter((r) => r.classList.contains('un') && ok(r, st) && !r.hidden) : [];
      readMany(rows, (g.querySelector('.p-fold .t') || {}).textContent || 'Day');
      return;
    }
    const ag = t.closest('a[data-n-agent]');
    if (ag && plain) { e.preventDefault(); chat(ag.getAttribute('data-n-agent')); return; }
    const act = t.closest('[data-n-act]');
    if (act) {
      const det = act.closest('.p-det'), id = det && det.getAttribute('data-p-det-for');
      const r = id ? d.querySelector(ROW + '[data-row-id="' + CSS.escape(id) + '"]') : null;
      if (!r) return;
      e.preventDefault();
      const a = act.getAttribute('data-n-act');
      if (a === 'recap') recap(r); else runItem(r, a);
    }
  });
  d.addEventListener('change', (e) => {
    const p = page(), t = e.target;
    if (!p || !t || !p.contains(t)) return;
    if (t.matches('input[type=hidden][name=un]')) apply(p, { un: t.value === '1' }, true);
    else if (t.matches('select[name=per]')) apply(p, { per: t.value }, true);
  });
  d.addEventListener('pm7:filter', (e) => {
    const p = page();
    if (!p || !e.detail || e.detail.name !== 'notifs') return;
    recount();
    syncUrl(p);
  });
  /* the message id of a row (its ⋯ Pin item), to find the same message in the other band */
  const msgIds = new WeakMap();
  function itemOf(r) {
    if (!msgIds.has(r)) {
      let id = '';
      try {
        const m = JSON.parse((r.querySelector(MENU) || {}).getAttribute('data-menu') || '[]');
        const pin = m.find((x) => x.id === 'pin');
        id = pin && pin.vals ? pin.vals.id : '';
      } catch (e) { id = ''; }
      msgIds.set(r, id);
    }
    return msgIds.get(r);
  }
  /* after a swap of the list: listkeys gives focus back to the same row id; when that row left this view (unpinned
     from the Pinned band), the same message in the other band gets it */
  let focused = null;
  function refocus() {
    const f = focused, a = d.activeElement, l = list();
    focused = null;
    if (!f || !l || (a && a !== d.body && d.contains(a))) return;
    const shown = pm7.$$(ROW, l).filter((r) => r.getClientRects().length);
    const r = shown.find((x) => x.getAttribute('data-row-id') === f.id) ||
      (f.item ? shown.find((x) => itemOf(x) === f.item) : null);
    if (!r) return;
    pm7.$$(ROW, l).forEach((x) => { x.tabIndex = x === r ? 0 : -1; });
    r.focus({ preventScroll: true });
  }

  /* opening a row's details marks it read; not when table.js re-opens it after a swap of the list */
  let swapping = false;
  d.addEventListener('pm7:det', (e) => {
    const r = rowOf(e.target);
    if (r && !swapping && e.detail && e.detail.open && page()) markRead(r);
  });
  d.addEventListener('htmx:beforeSwap', (e) => {
    const pn = panel();
    if (!pn || !e.detail || e.detail.target !== pn) return;
    swapping = true;
    const r = rowOf(d.activeElement);
    focused = r && d.activeElement === r ? { id: r.getAttribute('data-row-id'), item: itemOf(r) } : null;
  });
  d.addEventListener('n:chat', (e) => { const r = rowOf(e.target); if (r) chat(agentOf(r)); });
  d.addEventListener('n:tab', (e) => { const r = rowOf(e.target); if (r) tab(agentOf(r)); });
  /* a live reload (the stream's inbox event, or no event: load) waits while it would close a menu or drop the
     focus, and runs once that is over (checked again every 2 s while it waits; no timed poll otherwise) */
  let retry = 0;
  const busy = (pn) => {
    const a = d.activeElement;
    return !!((pm7.layers && pm7.layers.top && pm7.layers.top()) || (a && pn.contains(a) && !a.matches(ROW)));
  };
  function later(pn) {
    if (retry) return;
    retry = window.setTimeout(() => { retry = 0; if (busy(pn)) later(pn); else refreshList(); }, 2000);
  }
  d.addEventListener('htmx:beforeRequest', (e) => {
    const x = e.detail || {}, pn = panel(), ev = x.requestConfig && x.requestConfig.triggeringEvent;
    if (!pn || x.elt !== pn || (ev && ev.type !== 'pm:inbox') || !busy(pn)) return;
    e.preventDefault();
    later(pn);
  });
  d.addEventListener('htmx:afterSettle', (e) => {
    const pn = panel();
    if (!pn || !e.detail || !(e.detail.elt === pn || pn.contains(e.detail.elt))) return;
    swapping = false;
    restoreRecaps();
    recount();
    refocus();
  });
  pm7.on('pm:inbox', pm7.debounce(loadBadge, 1500));

  pm7.ready(() => {
    const p = page();
    if (p) {
      restore(p);
      recount();
      openFromUrl(p);
      if (pm7.list) {
        pm7.list.on('notifs', {
          enter(r, id, e) {
            if ((e && e.type === 'click') || !agentOf(r)) return !!(pm7.table && pm7.table.det(r));
            return chat(agentOf(r));
          },
          o(r) { return tab(agentOf(r)); }
        });
      }
    }
    const b = d.querySelector('[data-n-bell]');
    if (b && !b.querySelector('.b')) loadBadge();
  });
  pm7.notifs = { recount: recount, badge: setBadge, open: openBell };
})();
