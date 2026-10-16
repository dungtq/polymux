// Project settings panel. The tabs are server-rendered, so after a successful save the page reloads once
// and the panel is reopened (sessionStorage flag) to show the result.
(function () {
  'use strict';
  var K = 'pm.pjset.reopen';
  document.addEventListener('htmx:afterSwap', function (e) {
    /* hx-swap=outerHTML detaches e.detail.target; read the live node by id. Only "Saved." reloads, never "Refused: ..." or errors. */
    var d = e.detail || {}, el = d.elt;
    if (!el || !el.closest || !(el.closest('#pj-settings') || el.id === 'pj-settings')) return;
    var t = document.getElementById('pj-settings'), f = t && t.querySelector('.ps-flash');
    if (!f || !/^Saved/.test(f.textContent.trim())) return;
    try { sessionStorage.setItem(K, '1'); } catch (x) {}
    setTimeout(function () { location.reload(); }, 500);
  });
  var g = document.querySelector('a.pj-gear');
  try {
    if (g && sessionStorage.getItem(K) === '1') { sessionStorage.removeItem(K); g.click(); }
  } catch (x) {}
})();
