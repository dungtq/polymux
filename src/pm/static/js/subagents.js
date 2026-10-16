/* Sub-agents tab (agent page): "Show all" / "Show less" on an opened sub-agent's clamped prompt.
   Client only. The open state is the data-open attribute, which nodiff.js keeps when it re-fetches a
   running sub-agent's work in place; the button label is switched by CSS (subagents.css). */
(function () {
  'use strict';
  document.addEventListener('click', function (e) {
    var b = e.target.closest && e.target.closest('[data-sa-more]');
    if (!b) return;
    var p = b.closest('[data-sa-prompt]');
    if (!p) return;
    e.preventDefault();
    if (p.hasAttribute('data-open')) p.removeAttribute('data-open'); else p.setAttribute('data-open', '');
  });
})();
