/* In-page image viewer: a click on an attached image (a.chat-img) opens it in a modal <dialog> instead of a new tab.
   Zoom: mouse wheel, + / - / 0 keys, the toolbar buttons, double-click (fit <-> 2x), pinch on touch; drag to pan.
   Shown with pmDialog (dialog.js): Esc, the backdrop and the close button ([data-dlg-close]) close it; a click on the empty
   stage around the image closes it too. Without JS the link still opens the image (target=_blank).
   External file (strict CSP); no browser popups (docs/UI-RULES.md). */
(function () {
  'use strict';
  var d = document, MIN = 1, MAX = 8;
  function el(tag, cls, text) { var n = d.createElement(tag); if (cls) n.className = cls; if (text != null) n.textContent = text; return n; }
  function btn(label, text, cls) {
    var b = el('button', 'imgview-b' + (cls ? ' ' + cls : ''), text);
    b.type = 'button'; b.setAttribute('aria-label', label); b.title = label; return b;
  }

  function open(href, alt) {
    var dlg = el('dialog', 'imgview'), stage = el('div', 'imgview-stage'), img = el('img'), bar = el('div', 'imgview-bar');
    var s = 1, x = 0, y = 0, pts = {}, pinch = 0, drag = null;
    dlg.setAttribute('aria-label', alt || 'Image'); img.src = href; img.alt = alt || ''; img.draggable = false;
    var out = btn('Zoom out', '−'), inn = btn('Zoom in', '+'), fit = btn('Fit', '1:1'), x1 = btn('Close', '×', 'imgview-x');
    x1.setAttribute('data-dlg-close', '');
    [out, inn, fit, x1].forEach(function (b) { bar.appendChild(b); });
    stage.appendChild(img); dlg.appendChild(stage); dlg.appendChild(bar);

    function apply() {
      if (s <= MIN) { x = 0; y = 0; }
      img.style.transform = 'translate(' + x + 'px,' + y + 'px) scale(' + s + ')';
      dlg.classList.toggle('zoomed', s > MIN);
      fit.textContent = s > MIN ? Math.round(s * 100) + '%' : '1:1';
    }
    function zoom(f, cx, cy) {   // cx, cy: point (px from stage centre) that stays fixed
      var n = Math.min(MAX, Math.max(MIN, s * f)); f = n / s;
      x = cx - (cx - x) * f; y = cy - (cy - y) * f; s = n; apply();
    }
    function centre(e) {
      var r = stage.getBoundingClientRect();
      return [e.clientX - r.left - r.width / 2, e.clientY - r.top - r.height / 2];
    }
    out.addEventListener('click', function () { zoom(1 / 1.5, 0, 0); });
    inn.addEventListener('click', function () { zoom(1.5, 0, 0); });
    fit.addEventListener('click', function () { s = 1; apply(); });
    stage.addEventListener('wheel', function (e) {
      e.preventDefault(); var c = centre(e); zoom(e.deltaY < 0 ? 1.2 : 1 / 1.2, c[0], c[1]);
    }, { passive: false });
    stage.addEventListener('dblclick', function (e) {
      var c = centre(e); if (s > MIN) { s = 1; apply(); } else zoom(2, c[0], c[1]);
    });
    stage.addEventListener('pointerdown', function (e) {
      pts[e.pointerId] = [e.clientX, e.clientY];
      try { stage.setPointerCapture(e.pointerId); } catch (_) { /* synthetic pointer */ }
      var k = Object.keys(pts);
      if (k.length === 2) { pinch = Math.hypot(pts[k[0]][0] - pts[k[1]][0], pts[k[0]][1] - pts[k[1]][1]); drag = null; }
      else drag = { x: e.clientX - x, y: e.clientY - y, moved: false };
    });
    stage.addEventListener('pointermove', function (e) {
      if (!pts[e.pointerId]) return;
      pts[e.pointerId] = [e.clientX, e.clientY];
      var k = Object.keys(pts);
      if (k.length === 2 && pinch) {
        var dist = Math.hypot(pts[k[0]][0] - pts[k[1]][0], pts[k[0]][1] - pts[k[1]][1]);
        var r = stage.getBoundingClientRect();
        zoom(dist / pinch, (pts[k[0]][0] + pts[k[1]][0]) / 2 - r.left - r.width / 2,
          (pts[k[0]][1] + pts[k[1]][1]) / 2 - r.top - r.height / 2);
        pinch = dist;
      } else if (drag && s > MIN) {
        drag.moved = true; x = e.clientX - drag.x; y = e.clientY - drag.y; apply();
      }
    });
    function up(e) { delete pts[e.pointerId]; pinch = 0; if (!Object.keys(pts).length) drag = null; }
    stage.addEventListener('pointerup', up); stage.addEventListener('pointercancel', up);
    stage.addEventListener('click', function (e) { if (e.target === stage) dlg.close(); });  // the empty stage around the image
    dlg.addEventListener('keydown', function (e) {
      if (e.key === '+' || e.key === '=') zoom(1.5, 0, 0);
      else if (e.key === '-') zoom(1 / 1.5, 0, 0);
      else if (e.key === '0') { s = 1; apply(); }
    });
    dlg.addEventListener('close', function () { if (dlg.parentNode) dlg.parentNode.removeChild(dlg); });
    d.body.appendChild(dlg);
    window.pmDialog(dlg, { focus: x1 });
    apply();
  }

  d.addEventListener('click', function (e) {
    var a = e.target.closest && e.target.closest('a.chat-img');
    if (!a || e.button || e.metaKey || e.ctrlKey || e.shiftKey || typeof HTMLDialogElement === 'undefined' || !window.pmDialog) return;
    e.preventDefault();
    var im = a.querySelector('img');
    open(a.getAttribute('href'), im ? im.alt : '');
  });
})();
