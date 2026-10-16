/* Chat scroll on the agent page, the ended page and the Builder chat (normal chat order: oldest at the
   top, newest at the bottom, right above the composer). The scroller is the page, or the element marked
   [data-ci-scroll] (Builder's message pane). This file only
   1. keeps the view pinned to the bottom while the user is near it (first load, new items, the live
      strip growing) and never jumps when they scrolled up to read;
   2. keeps a row where it was while older items are inserted above it (Load older).
   Exported as window.pm.chatScroll for chat-items.js: follow(), bottom(), keep(el), restore(). */
(function () {
  'use strict';
  var NEAR = 96;          // px from the bottom that still counts as "at the bottom"
  var stick = true;       // follow new content; false once the user scrolls up
  var lastTop = 0;
  var anchor = null;      // {el, top}: Load older keeps this row where it was

  function box() { return document.querySelector('[data-ci-scroll]'); }
  function scroller() { return box() || document.scrollingElement || document.documentElement; }
  function chatShown() {
    var m = document.getElementById('ci');
    return !!m && m.getClientRects().length > 0;
  }
  function gap() { var s = scroller(); return s.scrollHeight - s.scrollTop - s.clientHeight; }
  function toBottom() {
    if (!chatShown()) return;
    var s = scroller();
    s.scrollTop = s.scrollHeight;
    lastTop = s.scrollTop;
  }

  function onScroll() {
    var top = scroller().scrollTop;
    if (gap() <= NEAR) stick = true;
    else if (top < lastTop - 2) stick = false;  // scrolled up: stop following (content growth only pushes down)
    lastTop = top;
  }
  window.addEventListener('scroll', onScroll, { passive: true });

  function follow() { if (stick && !anchor) toBottom(); }

  // Load older: remember the first row on screen, restore its position after the insert
  function keep(el) {
    stick = false;
    anchor = el ? { el: el, top: el.getBoundingClientRect().top } : null;
  }
  function restore() {
    if (!anchor) return;
    var a = anchor;
    anchor = null;
    if (a.el.isConnected) {
      var d = a.el.getBoundingClientRect().top - a.top;
      if (d) { scroller().scrollTop += d; }
    }
    lastTop = scroller().scrollTop;
  }

  document.addEventListener('htmx:beforeRequest', function (e) {
    var elt = e.detail && e.detail.elt;
    if (elt && elt.matches && elt.matches('[data-composer] [data-cmp-form]')) stick = true;  // sending: show the bottom
  });
  document.addEventListener('htmx:afterSettle', function (e) {
    var t = e.detail && e.detail.target;
    if (t && (t.id === 'chat' || t.id === 'sa-live')) follow();
  });

  // anything that changes the height while the user is at the bottom (images, details, fonts) keeps them there
  function observe() {
    var b = box();
    if (b) b.addEventListener('scroll', onScroll, { passive: true });
    // the live strip (#chat) is swapped whole and sits outside #ci: follow any change inside the box
    var queued = false;  // one follow per frame for a burst of DOM changes
    if (b && window.MutationObserver) new MutationObserver(function () {
      if (queued) return;
      queued = true;
      window.requestAnimationFrame(function () { queued = false; follow(); });
    }).observe(b, { childList: true, subtree: true });
    var m = document.querySelector('.chatmain') || document.getElementById('ci');
    if (m && window.ResizeObserver) new ResizeObserver(function () { follow(); }).observe(m);
    var tc = document.getElementById('tab-chat');
    if (tc) tc.addEventListener('change', function () { if (tc.checked) window.requestAnimationFrame(follow); });
  }
  if (document.readyState === 'loading') document.addEventListener('DOMContentLoaded', observe); else observe();

  var pm = window.pm = window.pm || {};
  pm.chatScroll = {
    follow: follow,
    bottom: function () { stick = true; anchor = null; toBottom(); },
    keep: keep,
    restore: restore
  };
})();
