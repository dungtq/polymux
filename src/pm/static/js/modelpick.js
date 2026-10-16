/* Composer model / effort pill (claude.ai style): one pill opens a menu (pm.popover, popup.js: placement, outside click,
   Esc, arrow keys; a bottom sheet on phones) with the current model, an Effort panel and a More models panel. The items are
   submit buttons inside the two htmx forms (POST /a/<id>/model | /effort), so this file only opens, switches panels (Esc in
   a panel goes back to the first one), and refreshes the pill after a successful switch. No fetch, no CSRF here. */
(function () {
  'use strict';
  function menuOf(w) { return w.querySelector('.mp-menu'); }
  function pillOf(w) { return w.querySelector('[data-mp-toggle]'); }
  function pop() { return window.pm && window.pm.popover; }
  var OPTS = { placement: 'top-start', role: 'menu' };
  function show(w, name) {  // switch panels; an open menu is placed again for its new height (popup.js open = re-place)
    w.querySelectorAll('.mp-panel').forEach(function (p) { p.hidden = p.getAttribute('data-mp-panel') !== name; });
    var m = menuOf(w);
    if (pop() && pop().isOpen(m)) pop().open(pillOf(w), m, OPTS);
  }
  function items(w) { return Array.prototype.slice.call(w.querySelectorAll('.mp-panel:not([hidden]) .mp-row')); }
  function text(el, sel, v) { var n = el.querySelector(sel); if (n) n.textContent = v; }
  function open(w) {
    var m = menuOf(w);
    show(w, 'main');
    pop().open(pillOf(w), m, OPTS);
    if (!m.__mpKeys) {  // Esc in a sub panel goes back to the first panel (before popup.js closes the menu)
      m.__mpKeys = true;
      m.addEventListener('keydown', function (ev) {
        var p = m.querySelector('.mp-panel:not([hidden])');
        if (ev.key !== 'Escape' || !p || p.getAttribute('data-mp-panel') === 'main') return;
        ev.preventDefault(); ev.stopPropagation();
        show(w, 'main'); var f = items(w)[0]; if (f) f.focus();
      });
    }
  }

  // after a successful switch: tick the item, refresh the pill and the "current model" row (the server swaps no markup)
  var pending = null;
  document.addEventListener('click', function (ev) {
    var t = ev.target, w = t && t.closest && t.closest('[data-mpick]');
    if (!w || !pop()) return;
    var m = menuOf(w);
    if (t.closest('[data-mp-toggle]')) { if (pop().isOpen(m)) pop().close(m); else open(w); return; }
    var go = t.closest('[data-mp-go]');
    if (go) { show(w, go.getAttribute('data-mp-go')); var f = items(w)[1] || items(w)[0]; if (f) f.focus(); return; }
    if (t.closest('[data-mp-back]')) { show(w, 'main'); var b = items(w)[0]; if (b) b.focus(); return; }
    if (t.closest('[data-mp-close]')) { pop().close(m); return; }
    var it = t.closest('button[type=submit].mp-row');
    // no focus move yet: htmx reads the clicked button's name/value during this click; close right after it
    if (it) { pending = it; window.setTimeout(function () { pop().close(m); }, 0); }
  });
  document.addEventListener('htmx:afterRequest', function (ev) {
    var d = ev.detail || {}, it = pending;
    if (!it || !d.elt || !d.elt.contains(it)) return;
    pending = null;
    var w = it.closest('[data-mpick]'), isEff = it.name === 'effort';
    pillOf(w).focus();
    if (!d.successful) return;
    it.closest('form').querySelectorAll('.mp-row[aria-checked]').forEach(function (r) { r.setAttribute('aria-checked', r === it ? 'true' : 'false'); });
    var label = it.getAttribute('data-mp-label') || it.value;
    if (isEff) { text(w, '[data-mp-pill-e]', label); text(w, '[data-mp-eff-v]', label); }
    else { text(w, '[data-mp-pill-m]', label); text(w, '[data-mp-cur-n]', label); text(w, '[data-mp-cur-s]', it.getAttribute('data-mp-sub') || it.value); }
  });
})();
