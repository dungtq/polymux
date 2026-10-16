/* dt Settings popup (PRD.md §6.11 "page and popup, same code", LAYOUT.md §5 Settings, DESIGN.md §7, §8 ⌘,;
   README A23): the Settings page's body in a modal dialog over any page. Markup: shell/_settings.html
   (dialog#sp-dlg: head with "Open as page" [data-sp-page] and ×, body [data-sp-body]); not on the Settings page itself
   (its ids would exist twice) nor in embed.
   It loads the core's GET /settings/panel?s=<id> (the dt _settings_panel.html: search box, nav, sections) into the
   body, lets htmx process it (lazy sections, pm7:refresh reloads) and runs pm7.settings.init(body) (js/settings.js),
   which then handles the nav, the search and every save there exactly as on the page (the nav keeps the URL; "Open
   as page" follows the section). The body is emptied on close.
   Globals: pm7.settings += {open(id?) -> Promise<bool>, close(), toggle(), isOpen()} (Object.assign onto
   js/settings.js's object).
   Openers: ⌘, (keys.js row "settings": toggle; it also fires while the popup is the top layer), [data-sp-open] (the
   sidebar foot's Settings; data-sp-open="<id>" opens that section; a modified click follows the link), the search
   command "Settings". On /settings there is no dialog: toggle() focuses the page's search box, open(id) shows that
   section. Layers: pm7.dialog (layers.js: Esc, backdrop, focus trap, focus return); confirms, menus, toasts and
   dialog#s-install stack on top of it and close first. */
(function () {
  'use strict';
  const pm7 = window.pm7, d = document;
  const ID = /^[\w-]{1,32}$/;
  let seq = 0;

  const dlg = () => d.getElementById('sp-dlg');
  const body = (D) => D.querySelector('[data-sp-body]');
  function isOpen() { const D = dlg(); return !!(D && D.open); }
  const el = pm7.el;
  function waiting(B) { B.replaceChildren(el('p.sp-wait', null, 'Loading…')); }
  function pageSearch() {
    const i = d.querySelector('#main input[data-p-filter="settings"]');
    if (!i) return false;
    i.focus();
    if (i.select) i.select();
    return true;
  }

  async function load(D, id) {
    const B = body(D), n = ++seq;
    let r = null;
    try { r = await pm7.fetch('/settings/panel' + (id ? '?s=' + encodeURIComponent(id) : '')); } catch (e) { r = null; }
    if (n !== seq || !D.open) return false;
    if (!r || !r.ok) {
      const why = await pm7.why(r, 'Could not load the settings');
      if (n !== seq || !D.open) return false;
      B.replaceChildren(el('p.sp-err', null, why));
      return false;
    }
    const html = await r.text();
    if (n !== seq || !D.open) return false;
    B.innerHTML = html;  // server-rendered, escaped template
    if (window.htmx) window.htmx.process(B);
    pm7.settings.init(B);
    if (pm7.keys) pm7.keys.fill(B);
    return true;
  }
  async function open(id) {
    const s = id && ID.test(String(id)) ? String(id) : '', D = dlg();
    if (!D) {  // the Settings page itself (or no shell): its own section, else the page
      if (pm7.$('#main #s-panel') && (!s || pm7.settings.show(s))) return pageSearch();
      window.location.assign('/settings' + (s ? '?s=' + encodeURIComponent(s) : ''));
      return false;
    }
    if (!D.open) {
      waiting(body(D));
      pm7.dialog(D, { opener: d.activeElement, onClose: closed });
    }
    const ok = await load(D, s);
    if (ok && D.open) {
      const f = D.querySelector(pm7.phone() ? '[data-act=close]' : 'input[data-p-filter="settings"]');
      if (f) f.focus({ preventScroll: true });
    }
    return ok;
  }
  function closed() {
    seq++;
    const D = dlg();
    if (D) waiting(body(D));  // no second copy of the Settings ids stays in the page
  }
  function close() { const D = dlg(); if (D && D.open) pm7.layers.close(D); }
  function toggle() {
    if (isOpen()) { close(); return; }
    if (dlg()) { open(''); return; }
    if (!pageSearch()) window.location.assign('/settings');
  }

  /* ---------- openers: [data-sp-open] (sidebar foot), ⌘, (keys.js) ---------- */
  d.addEventListener('click', (e) => {
    const a = e.target && e.target.closest ? e.target.closest('[data-sp-open]') : null;
    if (!a || !dlg() || e.metaKey || e.ctrlKey || e.shiftKey || e.altKey || e.button) return;
    e.preventDefault();
    open(a.getAttribute('data-sp-open') || '');
  });

  pm7.settings = Object.assign(pm7.settings || {}, { open: open, close: close, toggle: toggle, isOpen: isOpen });
})();
