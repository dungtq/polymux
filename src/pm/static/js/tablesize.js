/* Resizable Markdown table columns (chat replies `.md`, project docs `.pj-md`, the /md reader).
   Drag a header cell's right edge (or the left edge of the next one). On the first drag the table switches to
   table-layout:fixed with the current column widths as explicit pixel widths; after that only the dragged column
   and the table width change (the .md-table wrapper scrolls). Double-click an edge: back to automatic widths.
   Fully delegated on document (tables arrive by HTMX/SSE swaps and the Compact view moves nodes), so no re-run
   hook is needed and pages without tables cost one closest() per pointer move. Touch: a wider edge zone; the
   header row has touch-action:pan-y (tablesize.css) so a sideways drag there resizes instead of panning.
   Widths live on the DOM nodes only (a re-rendered reply starts automatic again). External file (strict CSP). */
(function () {
  'use strict';
  var d = document, SEL = '.md th, .pj-md th', MIN = 40, hot = null, drag = null;

  function zone(e) { return !e.pointerType || e.pointerType === 'mouse' ? 6 : 14; }
  // The th whose right edge is under the pointer, or null.
  function edgeOf(e) {
    var t = e.target && e.target.closest ? e.target.closest(SEL) : null;
    if (!t) return null;
    var r = t.getBoundingClientRect(), z = zone(e);
    if (r.right - e.clientX <= z && r.right - e.clientX >= -2) return t;
    var p = t.previousElementSibling;
    if (p && p.tagName === 'TH' && e.clientX - r.left <= z) return p;
    return null;
  }
  function setHot(th) {
    if (hot === th) return;
    if (hot) hot.classList.remove('ts-hot');
    hot = th;
    if (hot) hot.classList.add('ts-hot');
  }
  // First drag: freeze the current widths so the layout does not jump.
  function freeze(table, row) {
    if (table.classList.contains('ts-fixed')) return;
    var cells = Array.prototype.slice.call(row.children), ws = cells.map(function (c) { return c.getBoundingClientRect().width; });
    var total = table.getBoundingClientRect().width;
    cells.forEach(function (c, i) { c.style.width = ws[i] + 'px'; });
    table.style.minWidth = '0';
    table.style.width = total + 'px';
    table.style.tableLayout = 'fixed';
    table.classList.add('ts-fixed');
  }
  function reset(table) {
    table.classList.remove('ts-fixed');
    ['width', 'minWidth', 'tableLayout'].forEach(function (k) { table.style[k] = ''; });
    Array.prototype.forEach.call(table.querySelectorAll('th, td'), function (c) { c.style.width = ''; });
  }
  function swallowClick() {
    function stop(ev) { ev.stopPropagation(); ev.preventDefault(); d.removeEventListener('click', stop, true); }
    d.addEventListener('click', stop, true);
    setTimeout(function () { d.removeEventListener('click', stop, true); }, 0);
  }

  d.addEventListener('pointermove', function (e) {
    if (drag) {
      if (e.pointerId !== drag.id) return;
      var dx = e.clientX - drag.x;
      if (!drag.moved && Math.abs(dx) < 3) return;
      if (!drag.moved) { drag.moved = true; freeze(drag.table, drag.th.parentNode); drag.w = drag.th.getBoundingClientRect().width; drag.tw = drag.table.getBoundingClientRect().width; drag.table.classList.add('ts-drag'); }
      var w = Math.max(MIN, drag.w + dx);
      drag.th.style.width = w + 'px';
      drag.table.style.width = (drag.tw + w - drag.w) + 'px';
      e.preventDefault();
      return;
    }
    if (e.pointerType === 'mouse' && !e.buttons) setHot(edgeOf(e));
  }, { passive: false });

  d.addEventListener('pointerdown', function (e) {
    if (e.button !== 0 || drag) return;
    var th = edgeOf(e), table = th && th.closest('table');
    if (!table) return;
    drag = { id: e.pointerId, th: th, table: table, x: e.clientX, moved: false, w: 0, tw: 0 };
    setHot(th);
    try { th.setPointerCapture(e.pointerId); } catch (_) { /* synthetic pointer */ }
    if (e.pointerType === 'mouse') e.preventDefault();  // no text selection while dragging
  });

  function end(e) {
    if (!drag || (e && e.pointerId !== drag.id)) return;
    var moved = drag.moved;
    drag.table.classList.remove('ts-drag');
    try { drag.th.releasePointerCapture(drag.id); } catch (_) { /* already released */ }
    drag = null;
    if (!e || e.pointerType !== 'mouse') setHot(null);
    if (moved && e && e.type === 'pointerup') swallowClick();
  }
  d.addEventListener('pointerup', end);
  d.addEventListener('pointercancel', end);

  d.addEventListener('dblclick', function (e) {
    var th = edgeOf(e), table = th && th.closest('table');
    if (table && table.classList.contains('ts-fixed')) { reset(table); e.preventDefault(); }
  });
})();
