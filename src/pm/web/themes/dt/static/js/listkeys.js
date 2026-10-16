/* dt theme (pm v7) list keys (DESIGN.md §8 "List keys"; contract PARTS.md §7): J K move, ↵ open, O open in a
   tab, X / → row details, ← close them, E read / unread, P pin, on the page's keyboard list. The keys live in the one
   key table (pm7.keys.register, scope "list"): they dispatch there and show in the ? sheet while a list is on the
   page. This file adds NO keydown listener. It also runs a row's ↵ action on a click and its O action on a
   double-click, and keeps the focused row and the list's Tab stop across htmx swaps.
   Globals: pm7.list = {on(name, handlers) -> off(), act(row, key, e) -> bool, row() -> focused row | null}.
     handlers: {enter, o, x, e, p: (row, id, event) -> false when nothing was done}; they win over data attributes.
   Hooks: [data-list=<name>] the list; rows [data-row-id] inside it (data-href = the row's link); per key k in
     enter o x e p: data-key-<k> on the row, else on the list = "menu:<item id>" (run that ⋯ item, js/parts.js)
     | "event:<name>" (CustomEvent on the row, bubbles, detail {id, list, key}) | "href" (go to data-href) | "det"
     (toggle details) | any other text = a CSS selector inside the row, clicked; data-key-<k>-label on the list = the
     sheet text. Defaults: enter = href, x = details. Only one row per list is a Tab stop (tabindex 0). */
(function () {
  'use strict';
  const pm7 = window.pm7, d = document;
  const LIST = '[data-list]', ROW = '[data-row-id]';
  const ACTIVE = 'a[href], button, input, select, textarea, label, summary, [role=switch], [data-menu], [tabindex="0"]';
  const handlers = new Map(), curIds = new Map();
  const LABEL = { enter: 'Open', o: 'Open in a tab', x: 'Row details: show / hide', e: 'Mark read / unread',
    p: 'Pin / unpin' };

  const shown = (n) => n.getClientRects().length > 0;
  function nameOf(l) { return l.getAttribute('data-list') || ''; }
  function rows(l) { return pm7.$$(ROW, l).filter((r) => r.closest(LIST) === l && shown(r)); }
  function row() {
    const a = d.activeElement, r = a && a.closest ? a.closest(ROW) : null;
    return r && r.closest(LIST) ? r : null;
  }
  function list() {  // the list holding focus, else the page's first visible list
    const r = row();
    if (r) return r.closest(LIST);
    return pm7.$$('main ' + LIST).filter(shown)[0] || null;
  }
  function spec(r, k) {
    const l = r.closest(LIST);
    return r.getAttribute('data-key-' + k) || (l && l.getAttribute('data-key-' + k)) || '';
  }
  function has(k) {
    const l = list();
    if (!l) return false;
    const h = handlers.get(nameOf(l));
    return !!((h && h[k]) || l.hasAttribute('data-key-' + k) || l.querySelector('[data-key-' + k + ']') ||
      (k === 'x' && l.querySelector('.p-det')));
  }
  function label(k) {
    const l = list();
    return (l && l.getAttribute('data-key-' + k + '-label')) || LABEL[k];
  }

  /* ---------- focus: one Tab stop per list, remembered by row id ---------- */
  function rove(l, r) {
    const all = pm7.$$(ROW, l).filter((x) => x.closest(LIST) === l);
    const want = r || all.find((x) => x.getAttribute('data-row-id') === curIds.get(nameOf(l))) ||
      all.find((x) => x.tabIndex === 0) || all[0];
    all.forEach((x) => { x.tabIndex = x === want ? 0 : -1; });
    if (want) curIds.set(nameOf(l), want.getAttribute('data-row-id'));
  }
  function focusRow(r) {
    rove(r.closest(LIST), r);
    r.focus({ preventScroll: true });
    r.scrollIntoView({ block: 'nearest' });
  }
  function move(step) {
    const l = list();
    if (!l) return false;
    const all = rows(l), r = row();
    if (!all.length) return false;
    let i = r ? all.indexOf(r) : -1;
    if (i < 0) {
      const id = curIds.get(nameOf(l)), c = all.find((x) => x.getAttribute('data-row-id') === id);
      i = c ? all.indexOf(c) : (step > 0 ? 0 : all.length - 1);
    } else {
      i = Math.max(0, Math.min(all.length - 1, i + step));
    }
    focusRow(all[i]);
    return true;
  }

  /* ---------- actions ---------- */
  function safe(h) { return typeof h === 'string' && h[0] === '/' && h[1] !== '/' && h[1] !== '\\'; }
  function act(r, k, e) {
    const l = r && r.closest(LIST);
    if (!l) return false;
    const h = handlers.get(nameOf(l)), id = r.getAttribute('data-row-id');
    if (h && h[k]) return h[k](r, id, e) !== false;
    const s = spec(r, k) || (k === 'enter' ? 'href' : k === 'x' ? 'det' : '');
    if (!s) return false;
    if (s === 'href') {
      const href = r.getAttribute('data-href');
      if (!safe(href)) return false;
      window.location.assign(href);
      return true;
    }
    if (s === 'det') return !!(pm7.table && pm7.table.det(r));
    if (s.indexOf('menu:') === 0) return !!(pm7.menus && pm7.menus.run(pm7.$('[data-menu]', r), s.slice(5)));
    if (s.indexOf('event:') === 0) {
      r.dispatchEvent(new CustomEvent(s.slice(6), { bubbles: true, detail: { id: id, list: nameOf(l), key: k } }));
      return true;
    }
    const el = r.querySelector(s);
    if (!el) return false;
    el.click();
    return true;
  }
  /* a key on the focused row; strict = the row itself must have focus (not a button inside it) */
  function onRow(k, e, strict) {
    const r = row();
    if (!r || (strict && d.activeElement !== r)) return false;
    return act(r, k, e);
  }
  function details(e) {
    const r = row();
    if (!r || !pm7.table) return false;
    if (e.code === 'ArrowRight') return d.activeElement === r && pm7.table.det(r, true);
    if (e.code === 'ArrowLeft') return d.activeElement === r && pm7.table.det(r, false);
    return act(r, 'x', e);
  }

  /* ---------- clicks: the row is the link (↵); a double-click is O ---------- */
  function rowClick(e) {
    if (e.defaultPrevented || e.button || e.metaKey || e.ctrlKey || e.shiftKey || e.altKey) return null;
    const r = e.target && e.target.closest ? e.target.closest(ROW) : null;
    if (!r || !r.closest(LIST)) return null;
    const c = e.target.closest(ACTIVE);
    if (c && c !== r && r.contains(c)) return null;   // a control inside the row does its own thing
    if (e.target.closest('.p-det')) return null;
    return r;
  }
  d.addEventListener('click', (e) => {
    const r = rowClick(e);
    if (!r) return;
    rove(r.closest(LIST), r);
    if (e.detail > 1) return;
    if (act(r, 'enter', e)) e.preventDefault();
  });
  d.addEventListener('dblclick', (e) => {
    const r = rowClick(e);
    if (!r) return;
    const h = handlers.get(nameOf(r.closest(LIST)));
    if ((h && h.o) || spec(r, 'o')) act(r, 'o', e);
  });
  d.addEventListener('focusin', (e) => {
    const r = e.target && e.target.closest ? e.target.closest(ROW) : null;
    if (r && r.closest(LIST)) curIds.set(nameOf(r.closest(LIST)), r.getAttribute('data-row-id'));
  });

  /* ---------- swaps: the focused row (by id) gets focus back; every list keeps one Tab stop ---------- */
  let keep = null;
  d.addEventListener('htmx:beforeSwap', () => {
    const r = row();
    keep = r ? { list: nameOf(r.closest(LIST)), id: r.getAttribute('data-row-id') } : null;
  });
  d.addEventListener('htmx:afterSettle', () => {
    pm7.$$(LIST).forEach((l) => rove(l));
    const k = keep, a = d.activeElement;
    keep = null;
    if (!k || (a && a !== d.body && d.contains(a))) return;
    const l = d.querySelector('[data-list="' + CSS.escape(k.list) + '"]');
    const r = l && pm7.$$(ROW, l).find((x) => x.getAttribute('data-row-id') === k.id);
    if (r) { rove(l, r); r.focus({ preventScroll: true }); }
  });

  function on(name, h) {
    handlers.set(name, h);
    return () => { if (handlers.get(name) === h) handlers.delete(name); };
  }
  pm7.list = { on: on, act: act, row: row };

  pm7.ready(() => {
    pm7.$$(LIST).forEach((l) => rove(l));
    if (!pm7.keys || !pm7.keys.register) return;          // embed pages load no key table
    pm7.keys.register({ id: 'list', group: 'This list', active: () => !!list() }, [
      { id: 'list-next', label: 'Next row', keys: ['J'], run: () => move(1) },
      { id: 'list-prev', label: 'Previous row', keys: ['K'], run: () => move(-1) },
      { id: 'list-open', label: () => label('enter'), keys: ['Enter'], run: (e) => onRow('enter', e, true) },
      { id: 'list-tab', label: () => label('o'), keys: ['O'], run: (e) => onRow('o', e), active: () => has('o') },
      { id: 'list-det', label: () => label('x'), keys: ['X', 'ArrowRight'], run: details, active: () => has('x') },
      { id: 'list-close', label: 'Row details: close', keys: ['ArrowLeft'], run: details, active: () => has('x') },
      { id: 'list-read', label: () => label('e'), keys: ['E'], run: (e) => onRow('e', e), active: () => has('e') },
      { id: 'list-pin', label: () => label('p'), keys: ['P'], run: (e) => onRow('p', e), active: () => has('p') }
    ]);
  });
})();
