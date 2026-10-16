/* dt project quick look (issue #244): one project's folders in a modal over any page, opened from search (a project
   result) or any [data-peek=<project>] link. Markup: shell/_peek.html (dialog#x-peek in the Settings popup's frame:
   head with the name, [data-pk-meta], Open project [data-pk-open] and ×; body [data-pk-body]); not in embed.
   The body is GET /dt/peek/<name>?path=<rel> (project/_peek_list.html: breadcrumb + one grid table). A plain click on
   a folder (a[data-pk-dir], also the breadcrumb and "..") loads that folder in the popup; a file link and Open project
   open a new browser tab, or this tab when Settings › Display › Project quick look says so (the server renders the
   links' target; the body's data-pk-new sets Open project's). A modified click follows any link natively; a click on
   a row outside its link acts as that link. The body is emptied on close.
   Layers: pm7.dialog (layers.js: Esc, backdrop, focus trap, focus return); no key or Esc handler here.
   Globals: pm7.peek = {open(name, {opener}) -> bool, close(), isOpen(), name() -> the project shown ("" = closed)}. */
(function () {
  'use strict';
  const pm7 = window.pm7, d = document;
  let seq = 0, cur = '';

  const dlg = () => d.getElementById('x-peek');
  const el = pm7.el;
  const q = (root, sel) => root.querySelector(sel);
  const body = (D) => q(D, '[data-pk-body]');
  function isOpen() { const D = dlg(); return !!(D && D.open); }
  function mod(e) { return e.metaKey || e.ctrlKey || e.shiftKey || e.altKey || e.button; }
  function waiting(B) { B.replaceChildren(el('p.sp-wait', null, 'Loading…')); }
  function url(name, path) {
    return '/dt/peek/' + encodeURIComponent(name) + (path ? '?path=' + encodeURIComponent(path) : '');
  }

  /* the head's meta line and Open project's target from the loaded body (box = .pk-in; null = defaults) */
  function head(D, box) {
    const m = q(D, '[data-pk-meta]'), a = q(D, '[data-pk-open]');
    if (m) m.textContent = box ? box.getAttribute('data-pk-meta') || '' : '';
    if (!a) return;
    if (!box || box.getAttribute('data-pk-new') !== '0') {
      a.setAttribute('target', '_blank');
      a.setAttribute('rel', 'noopener');
    } else {
      a.removeAttribute('target');
      a.removeAttribute('rel');
    }
  }

  async function load(D, name, path) {
    const B = body(D), n = ++seq;
    let r = null;
    try { r = await pm7.fetch(url(name, path)); } catch (e) { r = null; }
    if (n !== seq || !D.open) return false;
    if (!r || !r.ok || r.status === 204) {
      const why = r && r.status === 204 ? 'Not available in this UI theme'
        : await pm7.why(r, 'Could not list this folder');
      if (n !== seq || !D.open) return false;
      B.replaceChildren(el('p.sp-err', { role: 'alert' }, why));
      return false;
    }
    const html = await r.text();
    if (n !== seq || !D.open) return false;
    B.innerHTML = html;  // server-rendered, escaped template
    B.scrollTop = 0;
    head(D, q(B, '.pk-in'));
    const f = q(B, '.pk-r:not(.k-up) a.nm') || q(B, '.pk-r a.nm') || q(D, '[data-act=close]');
    if (f) f.focus({ preventScroll: true });
    return true;
  }

  function open(name, opts) {
    const s = String(name || ''), D = dlg(), o = opts || {};
    if (!s) return false;
    if (!D) { window.location.assign('/p/' + encodeURIComponent(s)); return false; }
    cur = s;
    q(D, '[data-pk-name]').textContent = s;
    q(D, '[data-pk-open]').setAttribute('href', '/p/' + encodeURIComponent(s));
    head(D, null);
    waiting(body(D));
    if (!D.open) {
      pm7.dialog(D, { opener: o.opener || d.activeElement, focus: q(D, '[data-act=close]'), onClose: closed });
    }
    load(D, s, '');
    return true;
  }
  function closed() {
    seq++;
    cur = '';
    const D = dlg();
    if (D) { waiting(body(D)); head(D, null); }
  }
  function close() { const D = dlg(); if (D && D.open) pm7.layers.close(D); }

  /* clicks inside the popup: folders load in place, a row click acts as its link */
  function inside(e, t, D) {
    const dir = t.closest('a[data-pk-dir]');
    if (dir) {
      if (mod(e)) return;
      e.preventDefault();
      load(D, cur, dir.getAttribute('data-pk-dir') || '');
      return;
    }
    if (mod(e) || t.closest('a, button, input, select, textarea')) return;
    const row = t.closest('.pk-list [data-row-id]'), a = row && q(row, 'a.nm');
    if (!a) return;
    if (a.hasAttribute('data-pk-dir')) load(D, cur, a.getAttribute('data-pk-dir') || '');
    else a.click();
  }

  d.addEventListener('click', (e) => {
    const t = e.target && e.target.closest ? e.target : null;
    if (!t) return;
    const D = dlg();
    if (D && D.open && D.contains(t)) { inside(e, t, D); return; }
    const a = t.closest('[data-peek]');
    if (!a || !D || mod(e)) return;
    e.preventDefault();
    open(a.getAttribute('data-peek'), { opener: a });
  });

  pm7.peek = { open: open, close: close, isOpen: isOpen, name: () => cur };
})();
