/* Agent lists (Inbox, project page): clicking the agent id (or the chevron before it) expands the row's detail panel (recap, exit /
   resume). Only those two toggle; a click anywhere else on the row still opens the chat aside. Open rows are kept
   per agent id (pm.openset) and re-applied after every live refresh (nodiff / htmx rewrite the row attributes).
   The recap is fetched on first open and left alone by nodiff ([data-keep]). */
(function () {
  'use strict';
  var d = document, open = window.pm.openset('pm.rowx.open');
  function set(w, on) {
    var b = w.querySelector('.rx-tg'), nm = w.querySelector('.aname[data-rx]');
    if (on) { if (!w.hasAttribute('data-open')) w.setAttribute('data-open', '1'); } else w.removeAttribute('data-open');
    if (b) { b.setAttribute('aria-expanded', on ? 'true' : 'false'); }
    if (nm) nm.setAttribute('aria-expanded', on ? 'true' : 'false');
    var box = w.querySelector('.n-recap');
    if (on && box) window.pm.loadRecap(box);
  }
  /* the rows a "Show recap" button heads: its Idle / Ended fold, else the list after its heading */
  function rowsOf(btn) {
    var box = btn.closest('details.idle-fold, .ended-fold'), list = box || (btn.closest('.rx-head') || {}).nextElementSibling;
    return list ? Array.prototype.filter.call(list.querySelectorAll('.nwrap[data-key]'), function (w) { return w.querySelector('.rx-tg') && w.offsetParent !== null; }) : [];
  }
  function labels() {
    d.querySelectorAll('.rx-all').forEach(function (b) {
      var rows = rowsOf(b), all = rows.length > 0 && rows.every(function (w) { return open.has(w.getAttribute('data-key')); });
      b.textContent = all ? 'Hide recap' : 'Show recap'; b.setAttribute('aria-pressed', all ? 'true' : 'false');
    });
  }
  function apply() {
    d.querySelectorAll('.nwrap[data-key]').forEach(function (w) {
      if (w.querySelector('.rx-tg')) set(w, open.has(w.getAttribute('data-key')));
    });
    labels();
  }
  function toggleAll(btn, e) {
    e.preventDefault(); e.stopImmediatePropagation();  /* inside a <summary>: do not fold the section */
    var rows = rowsOf(btn), all = rows.length > 0 && rows.every(function (w) { return open.has(w.getAttribute('data-key')); });
    rows.forEach(function (w) { set(w, open.set(w.getAttribute('data-key'), !all)); });
    labels();
  }
  function toggle(t, e) {
    var ab = t.closest && t.closest('.rx-all');
    if (ab) { toggleAll(ab, e); return true; }
    var tg = t.closest && t.closest('.rx-tg, .aname[data-rx]'), w = tg && tg.closest('.nwrap[data-key]');
    if (!w) return false;
    e.preventDefault(); e.stopImmediatePropagation();
    set(w, open.toggle(w.getAttribute('data-key'))); labels();
    return true;
  }
  /* capture phase: runs before the drawer opens the chat aside from the row link */
  d.addEventListener('click', function (e) { toggle(e.target, e); }, true);
  d.addEventListener('keydown', function (e) {
    if ((e.key === 'Enter' || e.key === ' ') && e.target.classList && (e.target.classList.contains('rx-tg') || e.target.hasAttribute('data-rx'))) toggle(e.target, e);
  }, true);
  d.addEventListener('nodiff:swapped', apply);
  d.addEventListener('htmx:afterSettle', apply);
  apply();
})();
