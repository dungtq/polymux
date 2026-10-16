/* dt Projects list (PRD.md §6.4). Markup: templates/projects.html, _projects_list.html; parts JS: listkeys.js
   (J K ↵ P, row click = the project), filter.js (server filter box), parts.js (⋯ menus, their POSTs, toasts and the
   #pj refresh), table.js. This file adds only what is the list's own:
   - pin: button[data-pj-pin] in a row runs that row's ⋯ item "pin" (pm7.menus.run): one POST, toast and refresh
     path for the button, the P key and the menu (POST /p/<name>/pin, pinned=0|1).
   - New project: [data-pj-new] (head action, also after swaps) opens dialog#pj-new (js/newproject.js: pm7.pjnew).
   - Launch agent…: the row ⋯ item fires pj:launch; it opens the Launch dialog for the row's project
     (pm7.launch.open({project})), else (no dialog on the page) that project's Launch page.
   - live refresh: #pj reloads itself (hx-get = its own current URL); a refresh fired while the user is still typing
     sends the filter box's text, so the list never jumps back to the old filter. */
(function () {
  'use strict';
  const pm7 = window.pm7, d = document;

  d.addEventListener('click', (e) => {
    const t = e.target && e.target.closest ? e.target : null;
    if (!t) return;
    const pin = t.closest('#pj [data-pj-pin]');
    if (pin) {
      e.preventDefault();
      e.stopPropagation();  // not the row's click (open the project)
      const row = pin.closest('[data-row-id]'), m = row && row.querySelector('[data-menu]');
      if (m) pm7.menus.run(m, 'pin');
      return;
    }
    const nw = t.closest('[data-pj-new]');
    if (nw) {
      e.preventDefault();
      if (pm7.pjnew) pm7.pjnew.open(nw);
    }
  });

  d.addEventListener('pj:launch', (e) => {
    const project = (e.detail && e.detail.id) || '';
    if (!project) return;
    if (pm7.launch && pm7.launch.open) pm7.launch.open({ project: project });
    else window.location.assign('/p/' + encodeURIComponent(project) + '/launch');
  });

  d.addEventListener('htmx:configRequest', (e) => {
    const x = e.detail || {}, el = x.elt;
    if (!el || el.id !== 'pj' || !x.path || !pm7.filter) return;
    const u = new URL(x.path, window.location.href);
    if (u.origin !== window.location.origin) return;
    const q = pm7.filter.q('projects').trim().replace(/\s+/g, ' ');
    if (q) u.searchParams.set('q', q); else u.searchParams.delete('q');
    x.path = u.pathname + u.search;
  });
})();
