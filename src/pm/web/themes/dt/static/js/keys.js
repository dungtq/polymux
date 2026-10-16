/* dt theme (pm v7) keys: the ONE key table (DESIGN.md §8), its dispatch, the shortcuts sheet (#x-keys) and the
   [data-act=<verb>] click dispatch for the shell verbs side, pal, keys, mode, tabsheet, tabclose, tabnew, aside,
   close, stop (dialog#x-stop, closed again after its htmx POST succeeded).
   Globals: pm7.keys = {table, label(spec), commands(), sheet(), register(scope, rows) -> unregister(), fill(root)}.
   register(): page keys join this table (PARTS.md §7). scope = {id, group (sheet heading), active() -> bool};
   rows = [{id, label (text or () -> text), keys: [spec], run(e) -> false when nothing to do, active?() -> bool}].
   A scoped row fires and shows in the sheet only while scope.active() (and its own active()) is true; it is never a
   search command. Pages never add a keydown listener of their own (js/listkeys.js registers the list keys).
   Key specs: 'mod+shift+P' (mod = ⌘ on Apple, Ctrl elsewhere), 'G I' (sequence, 1.2 s window), '?'.
   Letters, digits, brackets and comma match KeyboardEvent.code (layout independent); ? / > and Esc match
   KeyboardEvent.key. A spec with mod also fires in text fields; single keys only when no text field has focus.
   Esc is layers.js (one listener). Reserved for the browser, never bound: ⌘1-9, ⌘T, ⌘N, ⌘W, ⌘⇧T, ⌘⇧[ ].
   Hooks: #x-keys (body: [data-keys-body], else .x-kbody, created when missing), kbd[data-k="<spec>"] (labels
   filled here, again after htmx swaps), [data-k-title="<spec>"] (" (<label>)" added to the title once),
   .x-nav a[data-nav=<key>] (G targets), input[data-p-filter] / [data-filter] / main input[type=search] (/),
   html.aside, html[data-theme] + storage pm.v7.shell.mode. A shortcut's toast is pm7.toast(text, label(spec)) (key
   chip): layers.js shapes it by html[data-ktoast] (Settings › Display › Shortcut helper). */
(function () {
  'use strict';
  const pm7 = window.pm7, d = document, R = d.documentElement;

  /* ---------- labels ---------- */
  const MAC = { mod: '⌘', shift: '⇧', alt: '⌥' }, PC = { mod: 'Ctrl', shift: 'Shift', alt: 'Alt' };
  const GLYPH = { Enter: '↵', ArrowLeft: '←', ArrowRight: '→', ArrowUp: '↑', ArrowDown: '↓' };
  function label(spec) {
    if (spec.indexOf(' ') > 0) return spec;          // a sequence: 'G I'
    const p = spec.split('+'), k = p.pop(), M = pm7.isApple ? MAC : PC;
    return p.map((x) => M[x] || x).concat(GLYPH[k] || k).join(pm7.isApple ? '' : '+');
  }
  function text(t) { return typeof t.label === 'function' ? t.label() : t.label; }
  function on(t) { return (!t.scope || t.scope.active()) && (!t.active || t.active()); }

  /* ---------- actions ---------- */
  const PAGES = [  // G targets; the hrefs are a fallback, the sidebar's own links win
    ['inbox', 'I', 'Inbox', '/'], ['projects', 'P', 'Projects', '/p'], ['tmux', 'T', 'tmux', '/tmux'],
    ['notifications', 'N', 'Notifications', '/ntfy'], ['backlog', 'B', 'Backlog', '/backlog'],
    ['agents', 'A', 'Agents', '/agents'], ['servers', 'S', 'Servers', '/servers']
  ];
  function go(href) { if (href) window.location.assign(href); }
  function goPage(key, dflt) {
    const a = d.querySelector('.x-nav a[data-nav="' + key + '"]');
    go(a ? a.getAttribute('href') : dflt);
  }
  function search(scope) { if (pm7.search) pm7.search.toggle(scope); }
  function tabs(verb, arg) { if (pm7.tabs) pm7.tabs[verb](arg); }
  function focusFilter() {
    const f = pm7.$$('input[data-p-filter], [data-filter], main input[type=search]')
      .map((n) => (n.matches('input') ? n : pm7.$('input', n)))
      .filter((n) => n && !n.disabled && !n.closest('[hidden]') && n.getClientRects().length)[0];
    if (!f) return false;
    f.focus();
    if (f.select) f.select();
    return true;
  }
  const MODES = ['light', 'dark', 'auto'];
  function cycleMode() {
    const now = R.getAttribute('data-theme'), next = MODES[(MODES.indexOf(now) + 1) % MODES.length];
    R.setAttribute('data-theme', next);
    pm7.store.set('shell.mode', next);
    pm7.toast('Theme: ' + next);
  }
  function asideOpen() { return R.classList.contains('aside'); }
  function setAside(on) {
    R.classList.toggle('aside', on);
    pm7.$$('[data-act=aside]').forEach((b) => b.setAttribute('aria-pressed', on ? 'true' : 'false'));
    pm7.layers.sync();
  }
  /* Stop all agents: the typed-PANIC dialog; its form posts with htmx into #flash */
  function stopAll(el) {
    const dl = d.getElementById('x-stop'), acct = el && el.closest('details');
    if (!dl) return;
    const f = pm7.$('form', dl);
    if (f) f.reset();
    pm7.dialog(dl, { opener: (acct && pm7.$('summary', acct)) || el });
  }
  d.addEventListener('htmx:afterRequest', (e) => {
    const x = e.detail || {}, dl = d.getElementById('x-stop');
    if (dl && x.successful && x.elt && dl.contains(x.elt)) pm7.layers.close(dl);
  });
  function keysOpen() { const L = pm7.layers.top(); return !!L && L.el.id === 'x-keys'; }
  function palOpen() { const L = pm7.layers.top(); return !!L && L.el.id === 'x-pal'; }
  function spOpen() { const L = pm7.layers.top(); return !!L && L.el.id === 'sp-dlg'; }
  function settings() {  // the popup (js/settings-popup.js); without it the page
    if (pm7.settings && pm7.settings.toggle) pm7.settings.toggle(); else goPage('settings', '/settings');
  }

  /* ---------- the table: one row per action; the sheet, the dispatch and the > commands all read it ---------- */
  const TABLE = [
    { id: 'side', group: 'Panels', label: 'Sidebar: open / collapse', keys: ['mod+B'],
      run: (e) => { if (pm7.side) pm7.side.toggle(!!e); } },
    { id: 'aside', group: 'Panels', label: 'Chat aside: show / hide', keys: [], run: () => setAside(!asideOpen()) },
    { id: 'mode', group: 'Panels', label: 'Theme: light, dark, auto', keys: [], run: cycleMode },
    { id: 'qchat', group: 'Panels', label: 'Quick chat: open / close', keys: ['Q'], run: () => false,  // qchat.js
      active: () => !!(pm7.qchat && d.getElementById('q-panel')) },
    { id: 'esc', group: 'Panels', label: 'Close the top layer / leave a text field', keys: ['Esc'], info: true },
    { id: 'search', group: 'Search', label: 'Search everything', keys: ['mod+shift+P', 'mod+K'], layer: palOpen,
      run: () => search('all') },
    { id: 'cmds', group: 'Search', label: 'Commands only (in search)', keys: ['>'], info: true },
    { id: 'filter', group: 'Search', label: 'Filter this list', keys: ['/'], run: focusFilter },
    { id: 'sessions', group: 'Sessions and tabs', label: 'Go to session', keys: ['mod+P'], layer: palOpen,
      run: () => search('sessions') },
    { id: 'prev', group: 'Sessions and tabs', label: 'Previous tab', keys: ['shift+[', 'mod+alt+['],
      run: () => tabs('step', -1) },
    { id: 'next', group: 'Sessions and tabs', label: 'Next tab', keys: ['shift+]', 'mod+alt+]'],
      run: () => tabs('step', 1) },
    { id: 'close', group: 'Sessions and tabs', label: 'Close tab', keys: ['alt+W'], run: () => tabs('closeCurrent') },
    { id: 'reopen', group: 'Sessions and tabs', label: 'Reopen closed tab', keys: ['alt+shift+T'],
      run: () => tabs('reopen') }
  ].concat(PAGES.map((p) => ({ id: 'go-' + p[0], group: 'Go to page', label: p[2], keys: ['G ' + p[1]],
    run: () => goPage(p[0], p[3]) })), [
    { id: 'settings', group: 'Go to page', label: 'Settings popup', keys: ['mod+,'], layer: spOpen, run: settings },
    { id: 'launch', group: 'Go to page', label: 'Launch an agent', keys: ['N'], run: () => go('/launch') },
    { id: 'keys', group: 'Go to page', label: 'Keyboard shortcuts (this sheet)', keys: ['?'], layer: keysOpen,
      run: () => sheet() }
  ]);

  /* ---------- dispatch ---------- */
  const CODE = { '[': 'BracketLeft', ']': 'BracketRight', ',': 'Comma', '.': 'Period' };
  const BYKEY = { '?': '?', '/': '/', '>': '>', Esc: 'Escape', Enter: 'Enter' };
  function parse(spec) {  // 'mod+alt+[' -> {mod, shift, alt, code | key}
    const p = spec.split('+'), k = p.pop();
    const o = { mod: p.indexOf('mod') >= 0, shift: p.indexOf('shift') >= 0, alt: p.indexOf('alt') >= 0 };
    if (BYKEY[k]) o.key = BYKEY[k];
    else o.code = CODE[k] || (/^[A-Z]$/.test(k) ? 'Key' + k : /^[0-9]$/.test(k) ? 'Digit' + k : k);
    return o;
  }
  const BINDS = [], SEQ = {};
  function bind(t) {
    t.keys.forEach((spec) => {
      if (t.info || !t.run) return;
      if (spec.indexOf(' ') > 0) SEQ[parse(spec.split(' ')[1]).code] = t;
      else BINDS.push({ t: t, o: parse(spec) });
    });
  }
  TABLE.forEach(bind);
  /* page keys (scoped): appended after the shell's, so a shell key always wins a clash */
  function register(scope, rows) {
    const added = (rows || []).map((r) => Object.assign({ group: scope.group }, r, { scope: scope }));
    added.forEach((t) => { TABLE.push(t); bind(t); });
    return function unregister() {
      added.forEach((t) => {
        const i = TABLE.indexOf(t);
        if (i >= 0) TABLE.splice(i, 1);
        for (let j = BINDS.length - 1; j >= 0; j--) if (BINDS[j].t === t) BINDS.splice(j, 1);
        Object.keys(SEQ).forEach((c) => { if (SEQ[c] === t) delete SEQ[c]; });
      });
    };
  }
  function hit(e, o) {
    const mod = pm7.isApple ? e.metaKey && !e.ctrlKey : e.ctrlKey && !e.metaKey;
    if (o.mod ? !mod : e.metaKey || e.ctrlKey) return false;
    if (e.altKey !== o.alt) return false;
    if (o.key) return e.key === o.key;               // ? / > need shift on many layouts: shift is not compared
    return e.shiftKey === o.shift && e.code === o.code;
  }
  let gAt = 0;
  d.addEventListener('keydown', (e) => {
    if (e.defaultPrevented || e.isComposing || e.key === 'Escape') return;
    const field = pm7.isField(e.target), L = pm7.layers.top();
    const plain = !e.metaKey && !e.ctrlKey && !e.altKey && !e.shiftKey;
    if (gAt) {
      const t = Date.now() - gAt <= 1200 && plain && !field && !L ? SEQ[e.code] : null;
      gAt = 0;
      if (t && on(t)) { e.preventDefault(); t.run(e); return; }
    }
    for (const b of BINDS) {
      if (!hit(e, b.o) || (field && !b.o.mod) || !on(b.t)) continue;
      if (L && !(b.t.layer && b.t.layer())) {           // a layer is on top: the key waits, ⌘P still never prints
        if (b.o.mod) e.preventDefault();
        return;
      }
      if (b.t.run(e) !== false) e.preventDefault();     // run returns false = nothing to do (/ without a filter)
      return;
    }
    if (plain && !field && !L && e.code === 'KeyG' && !e.repeat) gAt = Date.now();
  }, true);

  /* ---------- the shortcuts sheet (#x-keys) ---------- */
  function kbds(dd, spec) {
    spec.split(' ').forEach((part) => dd.appendChild(Object.assign(d.createElement('kbd'), {
      textContent: label(part) })));
  }
  function body(K) {
    let b = pm7.$('[data-keys-body]', K) || pm7.$('.x-kbody', K);
    if (!b) { b = d.createElement('div'); b.className = 'x-kbody'; K.appendChild(b); }
    return b;
  }
  function render(K) {
    const groups = [];
    TABLE.forEach((t) => {
      if (!t.keys.length || !on(t)) return;
      let g = groups.find((x) => x.name === t.group);
      if (!g) groups.push(g = { name: t.group, rows: [] });
      g.rows.push(t);
    });
    const note = d.createElement('p');
    note.className = 'x-knote';
    note.textContent = (pm7.isApple ? '⌘ is Command.' : 'Ctrl on Linux and Windows (⌘ on macOS and iPad).') +
      ' Single keys work when no text field has focus.';
    const cols = d.createElement('div');
    cols.className = 'cols';
    const half = Math.ceil(groups.length / 2);
    [groups.slice(0, half), groups.slice(half)].forEach((part) => {
      const col = d.createElement('div');
      part.forEach((g) => {
        const h = d.createElement('h3'), dl = d.createElement('dl');
        h.textContent = g.name;
        g.rows.forEach((t) => {
          const dt = d.createElement('dt'), dd = d.createElement('dd');
          dt.textContent = text(t);
          t.keys.forEach((spec) => kbds(dd, spec));
          dl.append(dt, dd);
        });
        col.append(h, dl);
      });
      cols.appendChild(col);
    });
    body(K).replaceChildren(note, cols);
  }
  function sheet() {
    const K = d.getElementById('x-keys');
    if (!K) return;
    if (keysOpen()) { pm7.layers.close(K); return; }
    render(K);
    pm7.dialog(K);
  }
  function fillLabels(root) {
    pm7.$$('kbd[data-k]', root).forEach((k) => { k.textContent = label(k.getAttribute('data-k')); });
    pm7.$$('[data-k-title]:not([data-k-titled])', root).forEach((n) => {
      const k = label(n.getAttribute('data-k-title'));
      n.setAttribute('data-k-titled', '');
      if (n.title && k) n.title += ' (' + k + ')';
    });
  }

  /* commands for search (`>`): every runnable row of the table */
  const CMD_LABEL = { keys: 'Keyboard shortcuts', qchat: 'Quick chat', settings: 'Settings' };
  function commands() {
    return TABLE.filter((t) => t.run && !t.info && !t.scope && (!t.active || t.active())).map((t) => ({
      id: t.id, label: CMD_LABEL[t.id] || (t.id.indexOf('go-') === 0 ? 'Go to ' + t.label : t.label),
      keys: t.keys.map(label), run: () => t.run()
    }));
  }

  /* ---------- [data-act=<verb>] clicks ---------- */
  const ACTS = {
    side: () => pm7.side && pm7.side.toggle(false),
    pal: () => search('all'),
    keys: () => sheet(),
    mode: () => cycleMode(),
    tabsheet: (el) => pm7.tabs && pm7.tabs.sheet(el),
    tabclose: (el) => {
      const t = el.closest('[data-tab-kind]');
      if (t && pm7.tabs) pm7.tabs.close(t.getAttribute('data-tab-kind'), t.getAttribute('data-tab-id'));
    },
    tabnew: () => search('sessions'),
    aside: () => setAside(!asideOpen()),
    close: (el) => pm7.layers.close(el),
    stop: (el) => stopAll(el)
  };
  d.addEventListener('click', (e) => {
    const el = e.target && e.target.closest ? e.target.closest('[data-act]') : null;
    const verb = el && el.getAttribute('data-act');
    if (!verb || !Object.prototype.hasOwnProperty.call(ACTS, verb)) return;  // other verbs belong to pages
    e.preventDefault();
    const menu = el.closest('details[open]');
    if (menu) menu.open = false;
    ACTS[verb](el, e);
  });

  pm7.keys = { table: TABLE, label: label, commands: commands, sheet: sheet, register: register, fill: fillLabels };
  d.addEventListener('htmx:afterSettle', (e) => fillLabels(e.target && e.target.isConnected ? e.target : d));
  pm7.ready(() => {
    fillLabels(d);
    pm7.layers.soft({ rank: 70, open: asideOpen, close: () => setAside(false), scrim: () => pm7.overlay() });
  });
})();
