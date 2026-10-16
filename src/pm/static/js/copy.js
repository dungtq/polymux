/* Copy button: <button data-copy-text="..."> copies the text (pm.copy, core.js); the label flips to "Copied" for a moment.
   No native popups. */
(function () {
  'use strict';
  var d = document;
  function flash(b, ok) {
    var old = b.getAttribute('data-label') || b.textContent;
    b.setAttribute('data-label', old);
    b.textContent = ok ? 'Copied' : 'Copy failed';
    setTimeout(function () { b.textContent = old; }, 1500);
  }
  d.addEventListener('click', function (e) {
    var b = e.target && e.target.closest && e.target.closest('[data-copy-text]');
    if (!b) return;
    e.preventDefault();
    window.pm.copy(b.getAttribute('data-copy-text') || '').then(function (ok) { flash(b, ok); });
  });
})();
