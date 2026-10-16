/* Theme auto/light/dark: localStorage 'pm-theme' -> <html data-theme>. Load in <head>, no defer. */
(function () {
  'use strict';
  if (window.__pmTheme) return;
  window.__pmTheme = true;
  var KEY = 'pm-theme', root = document.documentElement;
  function get() { try { return localStorage.getItem(KEY) || 'auto'; } catch (e) { return 'auto'; } }
  function apply(t) {
    if (t === 'light' || t === 'dark') root.setAttribute('data-theme', t);
    else root.removeAttribute('data-theme');
    var m = document.querySelector('meta[name="color-scheme"]');
    if (m) m.content = (t === 'light' || t === 'dark') ? t : 'light dark';
    var btns = document.querySelectorAll('[data-theme-set]');
    for (var i = 0; i < btns.length; i++) {
      btns[i].setAttribute('aria-pressed', btns[i].getAttribute('data-theme-set') === t ? 'true' : 'false');
    }
  }
  if (!document.querySelector('link[href="/static/css/settings.css"]')) {
    var l = document.createElement('link');
    l.rel = 'stylesheet'; l.href = '/static/css/settings.css';
    (document.head || root).appendChild(l);
  }
  apply(get());
  document.addEventListener('click', function (ev) {
    var tg = ev.target.closest && ev.target.closest('[data-theme-toggle]');
    var b = tg || (ev.target.closest && ev.target.closest('[data-theme-set]'));
    if (!b) return;
    var t;
    if (tg) {
      var cur = root.getAttribute('data-theme') ||
        (window.matchMedia && matchMedia('(prefers-color-scheme: dark)').matches ? 'dark' : 'light');
      t = cur === 'dark' ? 'light' : 'dark';
    } else t = b.getAttribute('data-theme-set');
    try { if (t === 'auto') localStorage.removeItem(KEY); else localStorage.setItem(KEY, t); } catch (e) { /* ignore */ }
    apply(t);
  });
  // same-origin drawer iframes and other tabs follow a toggle made elsewhere
  window.addEventListener('storage', function (ev) { if (ev.key === KEY || ev.key === null) apply(get()); });
  document.addEventListener('DOMContentLoaded', function () { apply(get()); });
})();
