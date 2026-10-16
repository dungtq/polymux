/* Jump rail (top / bottom): hidden by default, inset from the scrollbar. Shows while scrolling, on hover near the right edge or on
   keyboard focus, fades after ~1.5s idle. Top button only when not at the top, bottom only when not at the bottom.
   window.pmJumpRail(rail, getScroller, listenTargets): getScroller() -> the scrolling element (or null). */
(function () {
  'use strict';
  var IDLE = 1500, EDGE = 72;
  window.pmJumpRail = function (rail, getScroller) {
    if (!rail || rail.__jr) return rail && rail.__jr;
    var tb = rail.querySelector('[id="vc-top"],[data-page-jump="top"],[data-main-jump="top"]'), bb = rail.querySelector('[id="vc-bottom"],[data-page-jump="bottom"],[data-main-jump="bottom"]'), t = 0;
    function ends() {
      var el = getScroller(); if (!el) return { top: true, bottom: true };
      return { top: el.scrollTop <= 4, bottom: el.scrollTop + el.clientHeight >= el.scrollHeight - 4, none: el.scrollHeight <= el.clientHeight + 1 };
    }
    function sync() {
      var e = ends();
      if (tb) tb.hidden = e.top; if (bb) bb.hidden = e.bottom;
    }
    function fade() { if (rail.matches(':hover') || rail.contains(document.activeElement)) { t = setTimeout(fade, IDLE); return; } rail.classList.remove('is-on'); }
    function show() {
      sync();
      if (tb && tb.hidden && bb && bb.hidden) { rail.classList.remove('is-on'); return; }
      rail.classList.add('is-on'); clearTimeout(t); t = setTimeout(fade, IDLE);
    }
    rail.addEventListener('focusin', function () { sync(); rail.classList.add('is-on'); clearTimeout(t); });
    rail.addEventListener('focusout', function () { clearTimeout(t); t = setTimeout(fade, IDLE); });
    rail.addEventListener('mouseenter', function () { show(); });
    rail.__jr = { show: show, sync: sync, edge: EDGE };
    return rail.__jr;
  };
  /* full page: window scroll + pointer near the right edge */
  function page() {
    var rail = document.querySelector('.jump-rail');
    if (!rail || rail.closest('#vc') || document.getElementById('vc-top')) return;
    var se = function () { return document.scrollingElement || document.documentElement; };
    var jr = window.pmJumpRail(rail, se);
    if (!jr) return;
    window.addEventListener('scroll', jr.show, { passive: true });
    window.addEventListener('resize', jr.sync);
    document.addEventListener('mousemove', function (e) { if (e.clientX > window.innerWidth - jr.edge) jr.show(); }, { passive: true });
    jr.sync();
  }
  if (document.readyState === 'loading') document.addEventListener('DOMContentLoaded', page); else page();
})();

/* List pages with the chat aside: the page column scrolls on its own (main), the aside has its own scroller. This rail jumps
   the page column (or the window while the aside is closed) and sits at the right edge of that column. */
(function () {
  'use strict';
  function init() {
    var rail = document.getElementById('pg-rail'), main = document.querySelector('.v5-shell > main');
    if (!rail || !main || !window.pmJumpRail) return;
    var wide = window.matchMedia ? window.matchMedia('(min-width:700px)') : { matches: true };
    function own() { return document.body.classList.contains('chat-open') && wide.matches; }
    function sc() { return own() ? main : (document.scrollingElement || document.documentElement); }
    var jr = window.pmJumpRail(rail, sc);
    function place() { rail.style.right = own() ? Math.round(window.innerWidth - main.getBoundingClientRect().right + 28) + 'px' : ''; }
    function upd() { place(); jr.sync(); }
    /* any scroll inside the page column (capture: scroll does not bubble), wheel / touch over it, and the pointer near its right edge */
    document.addEventListener('scroll', function (e) {
      var t = e.target;
      if (own()) { if (t === main || (t.nodeType === 1 && main.contains(t))) { place(); jr.show(); } }
      else if (t === document || t === document.documentElement || t === document.body) jr.show();
    }, { passive: true, capture: true });
    function over(e) { return own() ? main.contains(e.target) : !(e.target.closest && e.target.closest('#v5chat,.v5-chat,.v5-side')); }
    document.addEventListener('wheel', function (e) { if (over(e)) { place(); jr.show(); } }, { passive: true });
    document.addEventListener('touchmove', function (e) { if (over(e)) { place(); jr.show(); } }, { passive: true });
    window.addEventListener('resize', upd);
    document.addEventListener('mousemove', function (e) {
      var r = own() ? main.getBoundingClientRect() : { right: window.innerWidth };
      if (e.clientX <= r.right && e.clientX > r.right - jr.edge) jr.show();
    }, { passive: true });
    rail.addEventListener('click', function (e) {
      var b = e.target.closest && e.target.closest('[data-main-jump]'), el = sc();
      if (!b) return;
      try { el.scrollTo({ top: b.getAttribute('data-main-jump') === 'bottom' ? el.scrollHeight : 0, behavior: 'auto' }); } catch (x) { el.scrollTop = b.getAttribute('data-main-jump') === 'bottom' ? el.scrollHeight : 0; }
      jr.show();
    });
    new MutationObserver(upd).observe(document.body, { attributes: true, attributeFilter: ['class'] });
    upd();
  }
  if (document.readyState === 'loading') document.addEventListener('DOMContentLoaded', init); else init();
})();
/* agent pages (full mode): the Jump to top / bottom buttons of the header scroll the page (once, even when loaded twice) */
(function () {
  if (window.pmPageJump) return;
  window.pmPageJump = true;
  document.addEventListener('click', function (e) {
    var b = e.target.closest && e.target.closest('[data-page-jump]');
    if (!b) return;
    var se = document.scrollingElement || document.documentElement;
    var top = b.getAttribute('data-page-jump') === 'bottom' ? se.scrollHeight : 0;
    try { window.scrollTo({ top: top, behavior: 'auto' }); } catch (err) { se.scrollTop = top; }
  });
})();
