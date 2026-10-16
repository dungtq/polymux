/* dt theme (pm v7) ticker: the ONE clock of the page (V7-CODE-7). Every second, while the page is visible, each
   [data-since="<epoch seconds>"] element gets its age as short text: 5s, 3m, 2h, 4d. The text goes into the
   element's [data-age] child, else its .a child (the session row's age slot), else the element itself when it has
   no child elements. Paused while the page is hidden; runs again at once when it is shown and after htmx swaps.
   Globals: pm7.ticker = {age(seconds) -> text, tick()}. */
(function () {
  'use strict';
  const pm7 = window.pm7, d = document;
  let timer = 0;

  function age(s) {
    s = Math.max(0, Math.floor(s));
    if (s < 60) return s + 's';
    if (s < 3600) return Math.floor(s / 60) + 'm';
    if (s < 86400) return Math.floor(s / 3600) + 'h';
    return Math.floor(s / 86400) + 'd';
  }
  function slot(n) {
    return n.querySelector('[data-age]') || n.querySelector('.a') || (n.children.length ? null : n);
  }
  function tick() {
    const now = Date.now() / 1000;
    d.querySelectorAll('[data-since]').forEach((n) => {
      let t = Number(n.getAttribute('data-since'));
      if (!t || !isFinite(t)) return;
      if (t > 1e12) t /= 1000;  // milliseconds given
      const out = slot(n), text = age(now - t);
      if (out && out.textContent !== text) out.textContent = text;
    });
  }
  function start() { if (!timer && !d.hidden) { tick(); timer = setInterval(tick, 1000); } }
  function stop() { if (timer) { clearInterval(timer); timer = 0; } }

  d.addEventListener('visibilitychange', () => { if (d.hidden) stop(); else start(); });
  d.addEventListener('htmx:afterSwap', tick);
  pm7.ticker = { age: age, tick: tick };
  pm7.ready(start);
})();
