/* Project page: expand / collapse the window list of each tmux row. Collapsed by default; the open rows are kept
   per session id (pm.openset) and re-applied after every live refresh (nodiff / htmx rewrite the row attributes). */
(function () {
  'use strict';
  var d = document, open = window.pm.openset('pm.srow.open');
  function set(row, on) {
    var b = row.querySelector('.srow-tg'), name = (row.querySelector('.c-main strong') || {}).textContent || 'session';
    if (on) { if (!row.hasAttribute('data-open')) row.setAttribute('data-open', '1'); } else row.removeAttribute('data-open');
    if (b) {
      b.setAttribute('aria-expanded', on ? 'true' : 'false');
      b.setAttribute('aria-label', (on ? 'Hide' : 'Show') + ' the windows of ' + name);
    }
  }
  function apply() {
    d.querySelectorAll('.srow[data-sid]').forEach(function (r) { set(r, open.has(r.getAttribute('data-sid'))); });
  }
  d.addEventListener('click', function (e) {
    var t = e.target, row = t.closest && t.closest('.srow'), id = row && row.getAttribute('data-sid');
    if (!row || !id) return;
    /* the chevron or anywhere on the row (name, counts, window list) toggles; links, buttons, forms and a text selection do not */
    var ctl = t.closest('a,button,form,input,select,label,summary');
    if (ctl && !ctl.classList.contains('srow-tg')) return;
    if (!ctl) { var sel = window.getSelection && window.getSelection(); if (sel && !sel.isCollapsed) return; }
    if (!id) return;
    e.preventDefault();
    set(row, open.toggle(id));
  });
  d.addEventListener('nodiff:swapped', apply);
  d.addEventListener('htmx:afterSettle', apply);
  apply();
})();
