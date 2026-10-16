/* The docked composer (.composer--dock: agent page, ended page): a fixed bar at the bottom of the viewport. Sets
 * --msgbar-h on <body> (the page keeps that much room under its content). Phones and touch screens: the bar starts hidden
 * (body.msgbar-hidden; a saved draft keeps it shown), a tap on the content shows / hides it, touch-scrolling with the
 * keyboard open and an empty box closes the keyboard, body.kbd + --kb follow the on-screen keyboard (visualViewport),
 * and a notice for the bar shows it again. Per-root plugin of composer.js. External file (strict CSP). */
(function () {
  'use strict';
  var pm = window.pm;
  if (!pm || !pm.composer || !pm.composer.use) return;
  // Touch-scroll >= HIDE_PX with the keyboard open (empty box) closes it; the keyboard is a visualViewport shrink > KBD_PX.
  var HIDE_PX = 24, KBD_PX = 100, TOUCH_MS = 1200;

  pm.composer.use(function (bar, st) {
    if (!bar.classList.contains('composer--dock') || bar.__dock) return;
    bar.__dock = true;
    var ta = st.ta, d = document, body = d.body, root = d.documentElement;
    var mq = window.matchMedia ? window.matchMedia('(pointer: coarse), (max-width: 699px)') : null;
    var rmq = window.matchMedia ? window.matchMedia('(prefers-reduced-motion: reduce)') : null;
    var vv = window.visualViewport || null;
    var hidden = false, kbd = false, baseH = 0, baseW = 0, lastY = null, lastH = 0, kacc = 0, quietUntil = 0;
    var touching = false, touchEnd = 0, raf = 0;

    function phone() { return !!(mq && mq.matches); }
    function scroller() { return d.scrollingElement || root; }
    function measure() { body.style.setProperty('--msgbar-h', bar.offsetHeight + 'px'); }
    function focused() { return d.activeElement === ta; }
    function touchy() { return touching || Date.now() - touchEnd < TOUCH_MS; }
    function resync() { var s = scroller(); lastY = s.scrollTop; lastH = s.scrollHeight; }
    function setHidden(h) {
      if (h === hidden || (h && !phone())) return;
      hidden = h;
      quietUntil = Date.now() + (rmq && rmq.matches ? 80 : 300);
      body.classList.toggle('msgbar-hidden', h);
      if (h) bar.setAttribute('aria-hidden', 'true'); else bar.removeAttribute('aria-hidden');
      resync();
    }
    // scrolling never hides or shows the bar (a tap on the content does); with the keyboard open it closes the keyboard
    function onScroll() {
      var s = scroller(), max = s.scrollHeight - root.clientHeight;
      if (!phone()) { setHidden(false); return; }
      if (max <= 0) { resync(); return; }
      var y = Math.min(Math.max(s.scrollTop, 0), max);  // drop rubber-band overscroll
      if (lastY === null || s.scrollHeight !== lastH || Date.now() < quietUntil) { lastY = y; lastH = s.scrollHeight; return; }
      var dy = y - lastY;
      lastY = y;
      if (!dy) return;
      if (focused() && !ta.value && touchy()) {
        kacc += Math.abs(dy);
        if (kacc >= HIDE_PX) { kacc = 0; ta.blur(); }
      } else kacc = 0;
    }
    window.addEventListener('scroll', onScroll, { passive: true });
    d.addEventListener('touchmove', onScroll, { passive: true });
    d.addEventListener('touchstart', function () { touching = true; }, { passive: true });
    function touchDone() { touching = false; touchEnd = Date.now(); }
    d.addEventListener('touchend', touchDone, { passive: true });
    d.addEventListener('touchcancel', touchDone, { passive: true });
    // a tap on the content toggles the bar on phones (the draft is kept; the iOS keyboard closes)
    d.addEventListener('click', function (e) {
      var t = e.target;
      if (t && t.closest && (bar.contains(t) || t.closest('.tabbar, a, button, summary, label, input, select, textarea, dialog, [role=dialog], [role=menu], .pop, .jump-rail, pre, code'))) return;
      var sel = window.getSelection && window.getSelection();
      if (sel && String(sel).length) return;  // selecting text, not tapping
      if (hidden) { setHidden(false); return; }
      if (!phone()) return;
      if (focused()) ta.blur();
      setHidden(true);
    });
    function vvUpdate() {
      raf = 0;
      var h = vv ? vv.height : window.innerHeight, w = vv ? vv.width : window.innerWidth;
      if (!baseH || Math.abs(w - baseW) > 1 || h > baseH) { baseH = h; baseW = w; }
      var k = phone() && (baseH - h > KBD_PX);
      var off = vv ? Math.max(0, Math.round(window.innerHeight - vv.height - vv.offsetTop)) : 0;
      body.style.setProperty('--kb', (k ? off : 0) + 'px');
      if (k !== kbd) {
        kbd = k;
        body.classList.toggle('kbd', k);
        if (k) setHidden(false);
        quietUntil = Date.now() + 400;  // ignore scroll deltas caused by the resize
        resync();
      }
    }
    function vvSchedule() { if (!raf) raf = window.requestAnimationFrame(vvUpdate); }
    // seed the baseline now: the first visualViewport event only comes once the keyboard is already open
    baseH = Math.max(vv ? vv.height : 0, window.innerHeight); baseW = vv ? vv.width : window.innerWidth;
    if (vv) { vv.addEventListener('resize', vvSchedule); vv.addEventListener('scroll', vvSchedule); }
    window.addEventListener('resize', function () { quietUntil = Date.now() + 400; vvSchedule(); });
    window.addEventListener('orientationchange', function () { baseH = 0; vvSchedule(); });
    ta.addEventListener('focus', function () { setHidden(false); vvSchedule(); });
    ta.addEventListener('blur', vvSchedule);
    // after a successful send the keyboard closes on touch devices, so the page can scroll / hide again
    bar.addEventListener('htmx:afterRequest', function (e) {
      if (e.detail && e.detail.successful && e.detail.elt === st.form && phone() && focused()) ta.blur();
    });
    // a notice for this bar: make sure it can be seen
    d.addEventListener('htmx:afterSwap', function (e) {
      if (e.detail && e.detail.target && bar.contains(e.detail.target)) setHidden(false);
    });
    if (mq && mq.addEventListener) mq.addEventListener('change', function () { if (!phone()) setHidden(false); vvSchedule(); });
    if (window.ResizeObserver) new ResizeObserver(measure).observe(bar);
    if (phone() && !ta.value && !focused()) setHidden(true);  // phones start hidden; a saved draft keeps it shown
    measure(); resync(); vvUpdate();
  });
})();
