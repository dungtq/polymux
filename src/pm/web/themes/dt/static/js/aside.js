/* dt theme (pm v7) chat aside (DESIGN.md §6.1, §7, §8; LAYOUT.md §2; PRD.md §6.1; SHELL.md §4): one agent chat, or a
   read-only terminal, at the right of any page, shown as a same-origin iframe of its embed page (/a/<id>?embed=1,
   /term/s<n>?embed=1; the core sends embed pages with frame-ancestors 'self', dt base.html renders them shell-less).
   Globals: pm7.aside = {open(id, {focus, state}) -> bool, close(), toggle(), current() -> id | null, fromTab(),
     toTab(), btw(id, question?) -> bool}.
     id = an agent id, or a tmux session id "$<n>" for a terminal (its page /term/s<n>, as shell.py tab_href).
     open: shows / switches the aside, focus (default true) puts the cursor into the chat's composer; on a phone
     (< 700 px) or on a session's own page (html.x-chatpg, README A28: no aside there; toggle and ⌘L only toast) it
     opens the session as a tab instead. state (optional, e.g. 'ended' from a list of past sessions): the head's
     state (then pm:agent_state); without it the dot of the session row, tab or a .p-gr list row on the page, else
     'unknown'. fromTab (⌘⇧L): the current session tab moves into the aside (pm7.tabs.close navigates to the home
     tab, whose page restores the aside; head.js never restores it on html.x-chatpg). toTab ("Open as tab"): the
     reverse.
     btw (README A9): the aside shows js/btw.js's side-question panel for that agent (head "btw · <id>", no tools,
     Open as tab off; not remembered across pages); question = asked at once. Phones (no aside): a new btw
     conversation in Quick chat, full screen (pm7.qchat.btw; no Use in composer there, Copy only).
   Reader: the aside chat's .md links (classic chat-md.js posts {pm: 'drawer', act: 'md', id, path}) open in the
     markdown reader dialog centred on the screen (js/mdview.js pm7.md.open; user issue #250).
   Keys (pm7.keys.register, group Panels): ⌘L on a session page (main .c-sess with a composer) while the aside is
   closed = that page's own composer (again: leave it; README A9); else ⌘L = the aside on the selected agent
   (focused list row with data-agent, data-href /a/<id> or in data-list=inbox; else the current session row; else a
   .sel list row; else the last one) with the cursor in its composer; open: a focused list row of another agent
   switches, else the composer (btw: its question box) gets the cursor, again (or from inside the aside) closes.
   ⌘⇧L = fromTab on a session tab, else toTab. ←/→ on the focused resize edge: 1 % wider / narrower.
   Clicks: [data-act=aside] (top bar toggle, head Close) and [data-act=astab] (head Open as tab) are taken here in
   the capture phase and stopped, so keys.js's plain html.aside toggle never runs; keys.js's "aside" command row
   is pointed at toggle(). Esc: a soft layer at rank 69 (DESIGN §7: last; keys.js's rank-70 one never sees it open).
   Storage (per browser): pm.v7.aside.open ("1" | "0"), pm.v7.aside.last (id), pm.v7.aside.pct (20-50, the share of
   the window; head.js applies it and html.aside before paint). Event: pm7:aside {open, id, btw?} on document.
   Hooks: aside#x-aside (shell/_aside.html) [data-aside-edge] [data-aside-dot] [data-aside-state] [data-aside-name]
   [data-aside-kind] [data-aside-body] (.fr while it holds iframe.x-aframe or the btw panel), html.aside,
   html.aside-drag, #x-sess a.x-sr[data-sess-id][data-sess-kind] .dot, #x-tabs .x-tab (state, kind, terminal name)
   and .p-gr[data-row-id] .dot (a list row of the agent: state only).
   Embed bridge: this file is also loaded in embed pages. In the aside's own iframe (window.frameElement inside
   #x-aside, same origin only) it registers a soft layer at rank 90 whose close posts {pm7: 'esc'} (so Esc closes
   the aside once the composer was left) and forwards ⌘L ⌘⇧L ⌘K ⌘P ⌘⇧P ⌘B ⌘, as {pm7: 'key', spec}; keys.js is not
   loaded there; the ⋯ item "Switch agent…" (event c:switch, handled by launch.js, not loaded there) is forwarded as
   {pm7: 'switch', id}, and the shell opens its Launch dialog (pm7.launch.open({handoff: id})). The parent accepts
   messages only from that iframe's window and its own origin. */
(function () {
  'use strict';
  const pm7 = window.pm7, d = document, R = d.documentElement;
  const COMPOSER = 'textarea[data-cmp-input], textarea[data-k-input]';
  const AGENT = /^[A-Za-z0-9][\w.:@-]{0,79}$/, TERM = /^\$\d{1,9}$/;
  const FWD = ['mod+L', 'mod+shift+L', 'mod+K', 'mod+shift+P', 'mod+P', 'mod+B', 'mod+,'];
  const CODES = { KeyL: 'L', KeyK: 'K', KeyP: 'P', KeyB: 'B', Comma: ',' };
  const STATES = ['running', 'waiting', 'failed', 'idle', 'unknown', 'ended'];
  const label = (spec) => (pm7.keys ? pm7.keys.label(spec) : '');

  /* ---------- embed page: the bridge to the parent shell ---------- */
  function inAside() {
    try {
      const f = window.frameElement;
      return !!(f && f.closest && f.closest('#x-aside'));
    } catch (e) { return false; }
  }
  if (d.body && d.body.classList.contains('x-embed')) {
    if (window.parent === window || !inAside()) return;
    const post = (m) => { try { window.parent.postMessage(m, window.location.origin); } catch (e) { /* gone */ } };
    pm7.ready(() => pm7.layers.soft({ rank: 90, open: () => true, close: () => post({ pm7: 'esc' }) }));
    d.addEventListener('keydown', (e) => {
      if (e.defaultPrevented || e.isComposing || e.altKey) return;
      const mod = pm7.isApple ? e.metaKey && !e.ctrlKey : e.ctrlKey && !e.metaKey, k = CODES[e.code];
      const spec = mod && k ? 'mod+' + (e.shiftKey ? 'shift+' : '') + k : '';
      if (FWD.indexOf(spec) < 0) return;
      e.preventDefault();
      post({ pm7: 'key', spec: spec });
    }, true);
    /* ⋯ Switch agent… (c:switch): launch.js is not loaded here, so the shell opens its Launch dialog */
    d.addEventListener('c:switch', (e) => {
      const s = e.target && e.target.closest ? e.target.closest('[data-agent]') : null;
      const id = s ? s.getAttribute('data-agent') : '';
      if (AGENT.test(id || '')) post({ pm7: 'switch', id: id });
    });
    return;
  }

  /* ---------- shell ---------- */
  const A = () => d.getElementById('x-aside');
  const q = (sel) => { const a = A(); return a ? a.querySelector(sel) : null; };
  let cur = null, opener = null, want = null;  // cur = {kind: agent | term | btw, id, href, state?}; want = iframe

  function isOpen() { return R.classList.contains('aside'); }
  function chatPage() { return R.classList.contains('x-chatpg'); }  // README A28: a session's own page, no aside
  function inside(n) { const a = A(); return !!(a && n && a.contains(n)); }
  function target(id) {
    id = id == null ? '' : String(id);
    if (TERM.test(id)) return { kind: 'term', id: id, href: '/term/s' + id.slice(1) };
    return AGENT.test(id) ? { kind: 'agent', id: id, href: '/a/' + encodeURIComponent(id) } : null;
  }
  const frameEl = () => q('[data-aside-body] iframe.x-aframe');

  /* ---------- head: state and kind from the session list or the tab strip ---------- */
  function sel(scope, attr, id) { return scope + '[' + attr + '="' + (window.CSS ? CSS.escape(id) : id) + '"]'; }
  function info(t) {
    const sr = t.kind === 'agent' ? d.querySelector(sel('#x-sess a.x-sr', 'data-sess-id', t.id)) : null;
    const tab = d.querySelector(sel('#x-tabs .x-tab[data-tab-kind="' + t.kind + '"]', 'data-tab-id', t.id));
    const row = t.kind === 'agent' && !sr && !tab ? d.querySelector(sel('.p-gr', 'data-row-id', t.id)) : null;
    const dot = [sr, tab, row].map((n) => n && n.querySelector('.dot')).find(Boolean);
    const nm = t.kind === 'term' && tab && tab.querySelector('.nm');
    return {
      state: (dot && STATES.find((s) => dot.classList.contains(s))) || '',
      kind: (sr && sr.getAttribute('data-sess-kind')) || (tab && tab.getAttribute('data-kind')) ||
        t.id.replace(/-[^-]*$/, ''),
      title: (nm && nm.textContent.trim()) || t.id
    };
  }
  function head(t, state) {
    const dot = q('[data-aside-dot]'), word = q('[data-aside-state]'), name = q('[data-aside-name]');
    const chip = q('[data-aside-kind]'), tab = q('[data-act=astab]'), bdi = name && (name.querySelector('bdi') || name);
    if (!dot || !word || !bdi || !chip || !tab) return;
    if (t && t.kind === 'btw') {  // a side question: no state, never a tab
      dot.hidden = true;
      word.textContent = ', side question, not sent to the agent';
      bdi.textContent = 'btw · ' + t.id;
      name.title = 'btw about ' + t.id + ' · nothing is sent to the agent';
      chip.textContent = 'no tools';
      chip.classList.remove('k');
      chip.removeAttribute('data-kind');
      chip.hidden = false;
      tab.disabled = true;
      return;
    }
    const x = t ? info(t) : null, term = !!t && t.kind === 'term';
    const st = state || (t && t.state) || (x && x.state) || 'unknown';
    STATES.forEach((s) => dot.classList.remove(s));
    dot.classList.add(st);
    dot.title = st;
    dot.hidden = !t || term;
    word.textContent = !t ? '' : term ? ', read-only terminal' : ', ' + st;
    bdi.textContent = x ? x.title : 'Chat aside';
    name.title = x ? x.title + (term ? ' · read-only terminal' : ' · ' + st) : '';
    chip.textContent = !t ? '' : term ? 'terminal' : x.kind;
    chip.classList.toggle('k', !!t && !term);
    if (t && !term) chip.setAttribute('data-kind', x.kind); else chip.removeAttribute('data-kind');
    chip.hidden = !chip.textContent;
    tab.disabled = !t;
  }

  /* ---------- the iframe ---------- */
  function focusIn(f) {  // true when the composer got the cursor
    if (!f.hasAttribute('data-ready')) { want = f; return true; }
    try {
      const doc = f.contentDocument, ta = doc && doc.querySelector(COMPOSER);
      if (f.contentWindow) f.contentWindow.focus();
      if (ta && !ta.disabled && ta.getClientRects().length) { ta.focus(); return true; }
    } catch (e) { /* not same-origin: leave focus where it is */ }
    return false;
  }
  function composerFocused(f) {
    try {
      const doc = f.contentDocument, a = doc && doc.activeElement;
      return !!(a && doc.hasFocus() && a.matches(COMPOSER));
    } catch (e) { return false; }
  }
  function frame(t, focus) {
    const body = q('[data-aside-body]'), src = t.href + '?embed=1';
    if (!body) return;
    unpanel();
    let f = frameEl();
    if (f && f.getAttribute('data-src') === src) { if (focus) focusIn(f); return; }
    if (f) f.remove();
    f = d.createElement('iframe');
    f.className = 'x-aframe';
    f.title = (t.kind === 'term' ? 'Terminal ' : 'Chat with ') + t.id;
    f.setAttribute('data-src', src);
    f.addEventListener('load', () => {
      f.setAttribute('data-ready', '');
      if (want === f) { want = null; focusIn(f); }
    });
    want = focus ? f : null;
    f.src = src;
    body.appendChild(f);
    body.classList.add('fr');
  }
  const panelEl = () => q('[data-aside-body] .x-btw');
  function unpanel() { const p = panelEl(); if (p) p.remove(); }  // btw.js keeps it (and its history) per agent
  function unframe() {
    const f = frameEl(), body = q('[data-aside-body]');
    if (f) f.remove();
    unpanel();
    if (body) body.classList.remove('fr');
    want = null;
  }

  /* ---------- open / close ---------- */
  function flag(on) {
    R.classList.toggle('aside', on);
    pm7.$$('[data-act=aside][aria-pressed]').forEach((b) => b.setAttribute('aria-pressed', on ? 'true' : 'false'));
    pm7.layers.sync();
  }
  function asTab(t) {
    if (window.location.pathname === t.href) return;  // already this page
    if (pm7.tabs) pm7.tabs.open(t.kind, t.id, t.href); else window.location.assign(t.href);
  }
  function open(id, opts) {
    const o = opts || {}, t = target(id);
    if (!t || !A()) return false;
    if (pm7.phone() || chatPage()) { asTab(t); return true; }  // LAYOUT §2, README A28: the chat opens as a tab
    if (!isOpen() && !inside(d.activeElement)) opener = d.activeElement;
    if (STATES.indexOf(o.state) >= 0) t.state = o.state;
    cur = t;
    head(t);
    frame(t, o.focus !== false);
    flag(true);
    if (!o.restore) pm7.store.set('aside.open', '1');
    pm7.store.set('aside.last', t.id);
    pm7.emit('pm7:aside', { open: true, id: t.id });
    return true;
  }
  function btw(id, question) {
    const t = target(id), body = q('[data-aside-body]');
    if (t && t.kind === 'agent' && pm7.phone()) {  // no aside on phones: Quick chat, full screen (README A9)
      if (pm7.qchat && pm7.qchat.btw) { pm7.qchat.btw(t.id, question || ''); return true; }
      pm7.toast('btw needs Quick chat, which is not on this page', '', { error: true });
      return false;
    }
    if (!t || t.kind !== 'agent' || !body || !pm7.btw) return false;
    if (!isOpen() && !inside(d.activeElement)) opener = d.activeElement;
    unframe();
    cur = { kind: 'btw', id: t.id, href: t.href };
    body.appendChild(pm7.btw.panel(t.id));
    body.classList.add('fr');
    head(cur);
    flag(true);
    pm7.emit('pm7:aside', { open: true, id: t.id, btw: true });
    if (question) pm7.btw.ask(t.id, question); else pm7.btw.focus(t.id);
    return true;
  }
  function showEmpty() {  // the toggle with no agent to show: the empty aside says how to fill it
    if (!A()) return;
    if (!isOpen() && !inside(d.activeElement)) opener = d.activeElement;
    head(null);
    flag(true);
  }
  function close() {
    if (!isOpen() && !cur) return;
    const t = cur, back = inside(d.activeElement);
    flag(false);
    unframe();
    cur = null;
    head(null);
    pm7.store.set('aside.open', '0');
    if (back) {
      const btn = d.querySelector('.x-asidebtn'), to = opener && d.contains(opener) ? opener : btn;
      if (to && to.getClientRects().length) to.focus({ preventScroll: true }); else d.activeElement.blur();
    }
    opener = null;
    pm7.emit('pm7:aside', { open: false, id: t ? t.id : null });
  }

  /* ---------- which agent: focused list row, current session row, selected row, last one ---------- */
  function agentOf(r) {
    if (!r) return null;
    if (r.hasAttribute('data-agent')) return r.getAttribute('data-agent');
    const m = /^\/a\/([^/?#]+)(?:[?#]|$)/.exec(r.getAttribute('data-href') || '');
    if (m) { try { return decodeURIComponent(m[1]); } catch (e) { return null; } }
    const l = r.closest('[data-list]');
    return l && l.getAttribute('data-list') === 'inbox' ? r.getAttribute('data-row-id') : null;
  }
  function focusedRow() {
    const a = d.activeElement;
    return a && a.closest ? agentOf(a.closest('[data-list] [data-row-id]')) : null;
  }
  function pick() {
    const sr = d.querySelector('#x-sess a.x-sr.cur[data-sess-id]');
    const ids = [focusedRow(), sr && sr.getAttribute('data-sess-id'),
      agentOf(d.querySelector('[data-list] [data-row-id].sel')), pm7.store.get('aside.last')];
    for (const id of ids) { const t = target(id); if (t) return t; }
    return null;
  }
  function toggle() {
    if (isOpen()) { close(); return; }
    if (chatPage()) { mainOnly(); return; }
    const t = pick();
    if (t) open(t.id); else showEmpty();
  }
  function mainOnly() { pm7.toast('No chat aside on a session page', label('mod+L')); }
  function ownComposer() {  // a session page's own composer (classic markup, README A5)
    const ta = d.querySelector('#main .c-sess textarea[data-cmp-input]');
    return ta && !ta.disabled && ta.getClientRects().length ? ta : null;
  }
  function lkey() {  // ⌘L in the shell (inside the aside the bridge closes it)
    const f = frameEl(), own = isOpen() ? null : ownComposer();
    if (own) {  // README A9: a session tab's ⌘L is its own composer, as in the mock (again: leave it)
      if (d.activeElement === own) { own.blur(); pm7.toast('Left the composer', label('mod+L')); return; }
      own.focus();
      pm7.toast('Composer', label('mod+L'));
      return;
    }
    if (isOpen() && cur && cur.kind === 'btw') {
      const row = target(focusedRow());
      if (row && row.id !== cur.id) { open(row.id); return; }
      if (!pm7.btw.focused(cur.id)) { pm7.btw.focus(cur.id); return; }
      close();
      return;
    }
    if (isOpen() && cur) {
      const row = target(focusedRow());
      if (row && row.id !== cur.id) { open(row.id); return; }
      if (f && !composerFocused(f) && focusIn(f)) return;
      close();
      return;
    }
    if (isOpen()) { close(); return; }
    if (chatPage()) { mainOnly(); return; }
    const t = pick();
    if (!t) { showEmpty(); return; }
    open(t.id);
    if (!pm7.phone()) pm7.toast('Chat aside · ' + t.id, label('mod+L'));
  }
  function fromTab() {
    const c = pm7.tabs && pm7.tabs.current(), kind = c ? c.getAttribute('data-tab-kind') : '';
    const t = kind === 'agent' || kind === 'term' ? target(c.getAttribute('data-tab-id')) : null;
    if (!t || t.kind !== kind) { pm7.toast('No session tab to move', label('mod+shift+L')); return false; }
    if (pm7.phone()) { pm7.toast('The chat aside is not used on phones'); return false; }
    pm7.store.set('aside.last', t.id);
    pm7.store.set('aside.open', '1');
    pm7.tabs.close(kind, t.id, { home: true });  // the current tab: to the home tab, whose page restores the aside
    return true;
  }
  function toTab() {
    if (!cur || cur.kind === 'btw') {
      pm7.toast(cur ? 'btw stays in the aside' : 'The chat aside is empty');
      return false;
    }
    const t = cur;
    close();
    asTab(t);
    return true;
  }
  function shiftL() {
    const c = pm7.tabs && pm7.tabs.current(), kind = c ? c.getAttribute('data-tab-kind') : '';
    if (kind === 'agent' || kind === 'term' || !cur || cur.kind === 'btw') fromTab(); else toTab();
  }

  /* ---------- resize edge: share of the window, 20-50 %, the CSS clamp keeps it >= 360 px ---------- */
  function pctNow() { const v = Number(pm7.store.get('aside.pct', 30)); return v >= 20 && v <= 50 ? v : 30; }
  function setPct(p, save) {
    const v = Math.max(20, Math.min(50, save ? Math.round(p) : p)), edge = q('[data-aside-edge]');
    R.style.setProperty('--aside-pct', v + 'vw');
    if (edge) edge.setAttribute('aria-valuenow', String(Math.round(v)));
    if (save) pm7.store.set('aside.pct', v);
    return v;
  }
  function reset() {
    R.style.removeProperty('--aside-pct');
    pm7.store.remove('aside.pct');
    const edge = q('[data-aside-edge]');
    if (edge) edge.setAttribute('aria-valuenow', '30');
    pm7.toast('Chat aside 30 %');
  }
  function wireEdge(h) {
    let drag = false, moved = false, last = pctNow();
    const at = (x) => (window.innerWidth - x) / window.innerWidth * 100;
    h.setAttribute('aria-valuenow', String(last));
    h.addEventListener('pointerdown', (e) => {
      if (e.button || pm7.overlay()) return;
      drag = true;
      moved = false;
      h.setPointerCapture(e.pointerId);
      h.classList.add('drag');
      R.classList.add('aside-drag');
      e.preventDefault();
    });
    h.addEventListener('pointermove', (e) => { if (drag) { moved = true; last = setPct(at(e.clientX)); } });
    const end = () => {
      if (!drag) return;
      drag = false;
      h.classList.remove('drag');
      R.classList.remove('aside-drag');
      if (moved) pm7.toast('Chat aside ' + setPct(last, true) + ' %');
    };
    h.addEventListener('pointerup', end);
    h.addEventListener('pointercancel', end);
    h.addEventListener('lostpointercapture', end);
    h.addEventListener('dblclick', reset);
  }
  const edgeFocused = () => { const h = q('[data-aside-edge]'); return !!h && d.activeElement === h; };
  function nudge(e) {  // the edge is the aside's left side: ← makes it wider
    pm7.toast('Chat aside ' + setPct(pctNow() + (e && e.code === 'ArrowRight' ? -1 : 1), true) + ' %');
  }

  /* ---------- wiring ---------- */
  d.addEventListener('click', (e) => {  // capture: keys.js (document, bubble) must not toggle html.aside itself
    const el = e.target && e.target.closest ? e.target.closest('[data-act=aside], [data-act=astab]') : null;
    if (!el || !A()) return;
    e.preventDefault();
    e.stopPropagation();
    if (el.getAttribute('data-act') === 'astab') toTab(); else toggle();
  }, true);
  window.addEventListener('message', (e) => {
    const f = frameEl(), m = e.data;
    if (!f || e.origin !== window.location.origin || e.source !== f.contentWindow) return;
    if (!m || typeof m !== 'object') return;
    if (m.pm === 'drawer' && m.act === 'md') { if (pm7.md) pm7.md.open(m.id, m.path); return; }
    if (m.pm7 === 'esc') { close(); return; }
    if (m.pm7 === 'switch') {  // the aside chat's ⋯ Switch agent…: Launch dialog with that chat as the prompt
      if (typeof m.id === 'string' && AGENT.test(m.id) && pm7.launch) pm7.launch.open({ handoff: m.id });
      return;
    }
    if (m.pm7 !== 'key' || FWD.indexOf(m.spec) < 0) return;
    if (m.spec === 'mod+L') { close(); return; }
    if (m.spec === 'mod+shift+L') { toTab(); return; }
    const row = pm7.keys && pm7.keys.table.find((t) => !t.scope && !t.info && t.run && t.keys.indexOf(m.spec) >= 0);
    if (row) row.run();
  });

  d.addEventListener('pm:agent_state', (e) => {
    const x = e.detail || {};
    if (cur && cur.kind === 'agent' && x.id === cur.id && STATES.indexOf(x.state) >= 0) head(cur, cur.state = x.state);
  });
  d.addEventListener('pm:agent_removed', (e) => {
    const x = e.detail || {};
    if (cur && cur.kind === 'agent' && x.id === cur.id) head(cur, cur.state = 'ended');
  });
  d.addEventListener('htmx:afterSettle', (e) => { if (cur && e.target && e.target.id === 'x-sess') head(cur); });

  pm7.aside = { open: open, close: close, toggle: toggle, current: () => (cur ? cur.id : null), fromTab: fromTab,
    toTab: toTab, btw: btw };
  pm7.ready(() => {
    if (!A()) return;
    const edge = q('[data-aside-edge]');
    if (edge) wireEdge(edge);
    pm7.layers.soft({ rank: 69, open: isOpen, close: close, scrim: () => pm7.overlay() });
    if (pm7.keys) {
      const row = pm7.keys.table.find((t) => t.id === 'aside');
      if (row) row.run = toggle;
      pm7.keys.register({ id: 'aside', group: 'Panels', active: () => true }, [
        { id: 'aside-l', label: 'Composer of this session, else the chat aside + its composer (again: close)',
          keys: ['mod+L'], run: lkey },
        { id: 'aside-move', label: 'Move the tab into the aside (and back)', keys: ['mod+shift+L'], run: shiftL },
        { id: 'aside-size', label: 'Chat aside wider / narrower (resize edge)', keys: ['ArrowLeft', 'ArrowRight'],
          active: edgeFocused, run: nudge }
      ]);
    }
    if (!isOpen()) return;  // head.js restored html.aside: show the remembered chat (no focus, no layout change)
    const t = target(pm7.store.get('aside.last'));
    if (t && !pm7.phone()) open(t.id, { focus: false, restore: true }); else flag(false);
  });
})();
