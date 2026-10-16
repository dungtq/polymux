/* Embedded pages (the chat aside iframe, ?embed) have no navbar search or palette. Keyboard focus is usually inside
   that iframe, so its key events never reach the parent: forward the global shortcuts to the parent page, which
   handles them in search.js / palette.js (same-origin postMessage only). */
(function () {
  'use strict';
  if (window.parent === window) return;
  function send(act) {
    try { window.parent.postMessage({ pm: 'shortcut', act: act }, location.origin); } catch (x) { /* detached */ }
  }
  document.addEventListener('keydown', function (e) {
    if (e.isComposing || e.altKey) return;
    var k = (e.key || '').toLowerCase();
    if (e.metaKey || e.ctrlKey) {
      if (k === 'p' && e.shiftKey) { e.preventDefault(); send('palette'); }
      else if (!e.shiftKey && (k === 'k' || k === 'p')) { e.preventDefault(); send('search'); }
      return;
    }
    if (e.key !== '/' || e.shiftKey) return;
    var t = e.target, tag = t && t.tagName;
    if (tag === 'INPUT' || tag === 'TEXTAREA' || tag === 'SELECT' || (t && t.isContentEditable)) return;
    e.preventDefault(); send('search');
  }, true);
})();
