/* dt theme (pm v7) sidebar: rail / open on wide screens (>= 900 px, stored as pm.v7.shell.rail = rail | open and
   applied before paint by head.js), overlay drawer under 900 px (html.side-open over #x-scrim, an Esc / scrim layer
   of layers.js at rank 40), and the sidebar's own filter box.
   Globals: pm7.side = {toggle(withToast), open(), close(), isOpen(), filter()}.
   Hooks: html.side-rail, html.side-open, aside#x-side, [data-act=side] (aria-expanded set here), input[data-sfilter],
   #x-sess (hx-get /dt/side) with details.x-sg > summary (project; data-project wins) and
   a.x-sr[data-sess-id] (label: data-label, else .nm, else title). The filter matches id, label and project only
   (never state words or ages), hides rows and empty groups, opens folded groups that match while it is set, and is
   re-applied after every htmx swap of #x-sess, keeping the list's scroll and the focused row. On pm7:resync (the
   stream reopened) the list is re-fetched through htmx with its own hx attributes. */
(function () {
  'use strict';
  const pm7 = window.pm7, d = document, R = d.documentElement;
  let opener = null;

  const side = () => d.getElementById('x-side');
  const sess = () => d.getElementById('x-sess');
  function isOpen() { return R.classList.contains('side-open'); }
  function expanded() {
    const on = pm7.overlay() ? isOpen() : !R.classList.contains('side-rail');
    pm7.$$('[data-act=side]').forEach((b) => b.setAttribute('aria-expanded', on ? 'true' : 'false'));
  }
  function label() { return pm7.keys ? pm7.keys.label('mod+B') : ''; }

  function open() {
    if (isOpen()) return;
    opener = d.activeElement;
    R.classList.add('side-open');
    pm7.layers.sync();
    expanded();
    const s = side(), first = s && (pm7.$('.x-nav a[aria-current]', s) || pm7.$('.x-nav a', s));
    if (first) first.focus({ preventScroll: true });  // not the filter: on a phone that pops the keyboard
  }
  function close() {
    if (!isOpen()) return;
    const s = side(), inside = s && s.contains(d.activeElement);
    R.classList.remove('side-open');
    pm7.layers.sync();
    expanded();
    if (inside && opener && d.contains(opener)) opener.focus({ preventScroll: true });
    opener = null;
  }
  function toggle(withToast) {
    if (pm7.overlay()) {
      if (isOpen()) close(); else open();
      if (withToast) pm7.toast(isOpen() ? 'Sidebar open' : 'Sidebar closed', label());
      return;
    }
    const rail = R.classList.toggle('side-rail');
    pm7.store.set('shell.rail', rail ? 'rail' : 'open');
    expanded();
    if (withToast) pm7.toast(rail ? 'Sidebar collapsed to icons' : 'Sidebar expanded', label());
  }

  /* ---------- filter ---------- */
  function norm(s) { return String(s || '').toLowerCase(); }
  function rowText(r) {
    const nm = r.querySelector('.nm');
    const lab = r.getAttribute('data-label') || (nm && nm.textContent) || r.getAttribute('title') || '';
    return norm(r.getAttribute('data-sess-id') + ' ' + lab + ' ' + (r.getAttribute('data-project') || ''));
  }
  function groupText(g) {
    const s = g.querySelector('summary');
    return norm(g.getAttribute('data-project') || (s && s.textContent));
  }
  function filter() {
    const box = sess(), inp = pm7.$('input[data-sfilter]');
    if (!box) return;
    const q = norm(inp && inp.value).trim();
    let shown = 0;
    pm7.$$('a.x-sr', box).forEach((r) => {
      const g = r.closest('details.x-sg');
      const m = !q || rowText(r).indexOf(q) >= 0 || (!!g && groupText(g).indexOf(q) >= 0);
      r.hidden = !m;
      if (m) shown++;
    });
    pm7.$$('details.x-sg', box).forEach((g) => {
      const any = pm7.$$('a.x-sr', g).some((r) => !r.hidden);
      g.hidden = !!q && !any;
      if (q && any && !g.open) { g.open = true; g.setAttribute('data-fopen', ''); }
      else if (!q && g.hasAttribute('data-fopen')) { g.open = false; g.removeAttribute('data-fopen'); }
    });
    let empty = pm7.$('.x-sempty', box);
    if (q && !shown) {
      if (!empty) {
        empty = d.createElement('p');
        empty.className = 'x-sempty';
        empty.textContent = 'No session matches.';
        box.appendChild(empty);
      }
    } else if (empty) {
      empty.remove();
    }
  }

  /* ---------- htmx swaps of #x-sess: keep scroll and focus, re-apply the filter ---------- */
  let saved = null;
  function isSess(e) {
    const x = e.detail || {};
    return [x.target, x.elt].some((n) => n && (n.id === 'x-sess' || (n.closest && n.closest('#x-sess'))));
  }
  d.addEventListener('htmx:beforeSwap', (e) => {
    if (!isSess(e)) return;
    const box = sess(), s = side(), a = d.activeElement;
    saved = { box: box ? box.scrollTop : 0, side: s ? s.scrollTop : 0,
      row: a && box && box.contains(a) ? a.getAttribute('data-sess-id') : null };
  });
  d.addEventListener('htmx:afterSwap', (e) => {
    if (!isSess(e)) return;
    filter();
    const box = sess(), s = side(), k = saved;
    saved = null;
    if (!k) return;
    if (box) box.scrollTop = k.box;
    if (s) s.scrollTop = k.side;
    if (k.row && box) {
      const r = pm7.$$('a.x-sr', box).find((n) => n.getAttribute('data-sess-id') === k.row);
      if (r) r.focus({ preventScroll: true });
    }
  });
  d.addEventListener('pm7:resync', () => {
    const box = sess();
    if (box && window.htmx && box.getAttribute('hx-get')) {
      window.htmx.ajax('GET', box.getAttribute('hx-get'), { source: box, target: box });
    }
  });
  d.addEventListener('input', (e) => { if (e.target && e.target.matches('input[data-sfilter]')) filter(); });
  window.addEventListener('resize', pm7.debounce(() => { if (!pm7.overlay()) close(); expanded(); }, 100));

  pm7.side = { toggle: toggle, open: open, close: close, isOpen: isOpen, filter: filter };
  pm7.ready(() => {
    pm7.layers.soft({ rank: 40, open: isOpen, close: close, scrim: true });
    expanded();
    filter();  // a value the browser restored into the box (back / forward)
  });
})();
