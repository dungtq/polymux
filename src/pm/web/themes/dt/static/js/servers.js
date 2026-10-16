/* dt theme (pm v7) Servers page (docs/themes/dt/PRD.md §6.9). Everything is server-rendered (servers.html, view model
   pm.web.dt.servers); this file only:
   - keeps the live refresh to the visible page (README A21, PARTS.md §13): #sv-panel polls every 10 s through its own
     hx-trigger; a timed request is cancelled while the browser tab is hidden or a layer (menu, confirm) is open, and
     the page refreshes once when it shows again. htmx allowEval is off, so this cannot sit in the trigger itself.
   - the panel's poll and refresh requests carry the filter box's text (?q=: Resources matches every row);
   - the head's Refresh ([data-sv-refresh]) sends pm7:refresh to #sv-panel and #sv-vms;
   - a visible row button or a row-details button [data-sv-run=<item id>] runs that item of the row's ⋯ menu
     (pm7.menus.run: the same confirm, POST, toast and refresh as the menu), a stack band's Restart stack
     [data-sv-stack] the "stack" item of the first row in that band.
   No timer, fetch, confirm or menu code of its own. */
(function () {
  'use strict';
  const pm7 = window.pm7, d = document;
  if (!pm7 || pm7.servers) return;
  const BOXES = ['sv-panel', 'sv-vms'];
  let missed = false;

  function refresh() {
    BOXES.forEach((id) => {
      const el = d.getElementById(id);
      if (el && window.htmx) window.htmx.trigger(el, 'pm7:refresh');
    });
  }

  d.addEventListener('htmx:beforeRequest', (e) => {
    const x = e.detail || {}, el = x.elt;
    if (!el || BOXES.indexOf(el.id) < 0) return;
    const timed = !(x.requestConfig && x.requestConfig.triggeringEvent);
    if (d.hidden || (timed && pm7.layers && pm7.layers.anyOpen())) {
      e.preventDefault();
      if (d.hidden) missed = true;
    }
  });
  // the panel's own requests (poll, refresh) carry the filter box's text: Resources filters every row on the server
  d.addEventListener('htmx:configRequest', (e) => {
    const x = e.detail || {}, el = x.elt;
    if (!el || el.id !== 'sv-panel' || !x.path || !pm7.filter) return;
    const u = new URL(x.path, window.location.href), v = pm7.filter.q('sv').trim();
    if (v) u.searchParams.set('q', v); else u.searchParams.delete('q');
    x.path = u.pathname + u.search;
  });
  d.addEventListener('visibilitychange', () => {
    if (d.hidden || !missed) return;
    missed = false;
    refresh();
  });

  function rowOf(b) {
    const row = b.closest('[data-row-id]');
    if (row) return row;
    const det = b.closest('[data-p-det-for]'), table = b.closest('.p-g');
    return det && table ? table.querySelector('[data-row-id="' + CSS.escape(det.getAttribute('data-p-det-for')) + '"]')
      : null;
  }
  const menuOf = (root) => (root ? root.querySelector('[data-menu]') : null);

  d.addEventListener('click', (e) => {
    const t = e.target;
    if (!t || !t.closest || !t.closest('.sv')) return;
    const b = t.closest('[data-sv-refresh], [data-sv-run], [data-sv-stack]');
    if (!b) return;
    e.preventDefault();
    if (b.hasAttribute('data-sv-refresh')) { refresh(); return; }
    if (b.hasAttribute('data-sv-run')) {
      pm7.menus.run(menuOf(rowOf(b)), b.getAttribute('data-sv-run'));
      return;
    }
    const grp = b.closest('.p-gg');
    pm7.menus.run(menuOf(grp && grp.querySelector('.p-gr')), 'stack');
  });

  pm7.servers = { refresh: refresh };
})();
