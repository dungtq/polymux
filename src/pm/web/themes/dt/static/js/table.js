/* dt theme (pm v7) grid tables (DESIGN.md §6.8, §6.9; contract PARTS.md §6): foldable group bands and row
   details, one open row per table, both kept across htmx swaps. Sorting is server-side (header links, ?sort=); the
   look is css/table.css, row keys and row clicks are js/listkeys.js.
   Globals: pm7.table = {det(row, open?) -> bool, fold(button | group, open?) -> bool, restore(root?)}.
     det(): toggles (open undefined) or sets a row's details; opening one closes the table's other open row; false
       when the row has no .p-det. fold(): the same for a group band.
   Hooks: .p-g[data-p-table=<key>] (state key; without it nothing is remembered) > .p-gg[data-p-grp=<key>] (.fold)
     > .p-grp button.p-fold[aria-expanded]; .p-gr[data-row-id] rows (.open while shown); .p-det[data-p-det-for=<row id>]
     [hidden] details, optional data-p-det-src=<url> (loaded with htmx.ajax into its .p-detc on first open, again after
     a swap brought a fresh one); [data-p-det-btn] in a row toggles its details (aria-expanded mirrored).
   State: in memory for the page's life (the server renders the first state, L-1); after any htmx swap the user's
   choices are applied again. Events (bubbling): pm7:det on the row {id, open}, pm7:fold on the group {key, open}. */
(function () {
  'use strict';
  const pm7 = window.pm7, d = document;
  const mem = new Map();   // table key -> {det: row id | null | undefined, folds: Map(group key -> open)}

  function state(t) {
    const k = t && t.getAttribute('data-p-table');
    if (!k) return null;
    if (!mem.has(k)) mem.set(k, { det: undefined, folds: new Map() });
    return mem.get(k);
  }
  function rowId(r) { return r.getAttribute('data-row-id'); }
  function detOf(row) {
    const t = row.closest('.p-g'), id = rowId(row);
    const n = row.nextElementSibling;
    if (n && n.matches('.p-det') && n.getAttribute('data-p-det-for') === id) return n;
    return t ? t.querySelector('.p-det[data-p-det-for="' + CSS.escape(id) + '"]') : null;
  }
  function rowOf(t, id) { return t.querySelector('.p-gr[data-row-id="' + CSS.escape(id) + '"]'); }

  function load(det) {
    const src = det.getAttribute('data-p-det-src');
    if (!src || det.hasAttribute('data-p-det-loaded') || !window.htmx) return;
    det.setAttribute('data-p-det-loaded', '');
    window.htmx.ajax('GET', src, { target: det.querySelector('.p-detc') || det, swap: 'innerHTML', source: det });
  }
  /* show / hide one row's details; no memory, no closing of others */
  function show(row, on) {
    const det = detOf(row);
    if (!det) return false;
    const was = !det.hidden;
    det.hidden = !on;
    row.classList.toggle('open', on);
    pm7.$$('[data-p-det-btn]', row).forEach((b) => b.setAttribute('aria-expanded', on ? 'true' : 'false'));
    if (on) load(det);
    if (was === on) return true;
    row.dispatchEvent(new CustomEvent('pm7:det', { bubbles: true, detail: { id: rowId(row), open: on } }));
    return true;
  }
  function det(row, open) {
    row = row && row.closest ? row.closest('.p-gr[data-row-id]') : null;
    const det0 = row && detOf(row);
    if (!det0) return false;
    const on = open === undefined ? det0.hidden : !!open, t = row.closest('.p-g');
    if (on && t) pm7.$$('.p-gr.open[data-row-id]', t).forEach((r) => { if (r !== row) show(r, false); });
    show(row, on);
    const st = state(t);
    if (st) st.det = on ? rowId(row) : null;
    return true;
  }

  function setFold(g, open) {
    const b = pm7.$('.p-grp .p-fold', g);
    g.classList.toggle('fold', !open);
    if (b) b.setAttribute('aria-expanded', open ? 'true' : 'false');
  }
  function fold(x, open) {
    const g = x && x.closest ? x.closest('.p-gg') : null;
    if (!g) return false;
    const on = open === undefined ? g.classList.contains('fold') : !!open;
    setFold(g, on);
    const st = state(g.closest('.p-g')), key = g.getAttribute('data-p-grp');
    if (st && key) st.folds.set(key, on);
    g.dispatchEvent(new CustomEvent('pm7:fold', { bubbles: true, detail: { key: key, open: on } }));
    return true;
  }

  /* after a swap: the user's choices win over the fresh markup; a fresh lazy .p-det that is open loads again */
  function restore(root) {
    const r = root && root.nodeType === 1 ? root : d;
    const tables = r.matches && r.matches('.p-g') ? [r] : pm7.$$('.p-g', r);
    const up = r.closest ? r.closest('.p-g') : null;
    if (up && tables.indexOf(up) < 0) tables.push(up);
    tables.forEach((t) => {
      const st = mem.get(t.getAttribute('data-p-table') || '');
      if (st) {
        st.folds.forEach((open, key) => {
          const g = t.querySelector('.p-gg[data-p-grp="' + CSS.escape(key) + '"]');
          if (g && g.classList.contains('fold') === open) setFold(g, open);
        });
        if (st.det !== undefined) {
          pm7.$$('.p-gr.open[data-row-id]', t).forEach((row) => { if (rowId(row) !== st.det) show(row, false); });
          const row = st.det ? rowOf(t, st.det) : null;
          if (row) show(row, true);
        }
      }
      pm7.$$('.p-det:not([hidden])[data-p-det-src]', t).forEach(load);
    });
  }

  d.addEventListener('click', (e) => {
    const t = e.target;
    if (!t || !t.closest || e.defaultPrevented) return;
    const f = t.closest('.p-grp .p-fold');
    if (f) { e.preventDefault(); fold(f); return; }
    const b = t.closest('[data-p-det-btn]');
    if (b) { e.preventDefault(); det(b); }
  });
  d.addEventListener('htmx:afterSettle', () => restore(d));  // an outerHTML swap's target is detached: scan all

  pm7.table = { det: det, fold: fold, restore: restore };
  pm7.ready(() => restore(d));
})();
