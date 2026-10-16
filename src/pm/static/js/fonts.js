/* Fonts: localStorage 'pm-fonts' (JSON) -> CSS vars on <html>. Load in <head>, no defer.
   ui/chat/mono family + ui scale + chat size. Per browser, like the theme. */
(function () {
  'use strict';
  if (window.__pmFonts) return;
  window.__pmFonts = true;
  var KEY = 'pm-fonts', root = document.documentElement;
  var FAM = {
    system: 'system-ui, -apple-system, "Segoe UI", sans-serif',
    sans: '"Helvetica Neue", Arial, "Liberation Sans", sans-serif',
    serif: 'Georgia, "Times New Roman", "Liberation Serif", serif',
    mono: 'ui-monospace, SFMono-Regular, "SF Mono", Menlo, Consolas, "Liberation Mono", monospace'
  };
  var DEF = { ui: 'system', chat: 'same', mono: 'mono', uiScale: 100, chatSize: 0, mdSize: 0, composeSize: 0 };
  function get() {
    var o = {};
    try { o = JSON.parse(localStorage.getItem(KEY) || '{}') || {}; } catch (e) { o = {}; }
    var r = {};
    for (var k in DEF) r[k] = (o[k] !== undefined && o[k] !== null && o[k] !== '') ? o[k] : DEF[k];
    return r;
  }
  function fam(v, fallback) {
    if (FAM[v]) return FAM[v];
    if (v === 'same') return fallback;
    // custom: a single family name typed by the user, quotes/specials stripped
    var n = String(v).replace(/[^A-Za-z0-9 _.\-]/g, '').trim();
    return n ? '"' + n + '", ' + fallback : fallback;
  }
  function apply(s) {
    var ui = fam(s.ui, FAM.system), mono = fam(s.mono, FAM.mono);
    var st = root.style;
    st.setProperty('--ui-font', ui);
    st.setProperty('--mono', mono);
    st.setProperty('--chat-font', fam(s.chat, ui));
    var sc = Math.max(70, Math.min(160, parseInt(s.uiScale, 10) || 100));
    st.fontSize = sc === 100 ? '' : (16 * sc / 100) + 'px';
    var cs = parseInt(s.chatSize, 10) || 0;
    if (cs >= 10 && cs <= 32) st.setProperty('--chat-size', cs + 'px'); else st.removeProperty('--chat-size');
    var ms = parseInt(s.mdSize, 10) || 0;
    if (ms >= 10 && ms <= 32) st.setProperty('--md-size', ms + 'px'); else st.removeProperty('--md-size');
    var zs = parseInt(s.composeSize, 10) || 0;
    if (zs >= 10 && zs <= 32) { st.setProperty('--compose-size', zs + 'px'); root.setAttribute('data-compose-size', ''); } else { st.removeProperty('--compose-size'); root.removeAttribute('data-compose-size'); }
    var zv = document.querySelectorAll('[data-chat-zoom-val]');
    for (var i = 0; i < zv.length; i++) zv[i].textContent = (cs >= 10 && cs <= 32) ? cs + 'px' : 'auto';
    var mv = document.querySelectorAll('[data-md-zoom-val]');
    for (var j = 0; j < mv.length; j++) mv[j].textContent = (ms >= 10 && ms <= 32) ? ms + 'px' : 'auto';
    var f = document.getElementById('fonts-form');
    if (f) for (var k in DEF) { var el = f.querySelector('[name="' + k + '"]'); if (el) el.value = s[k]; }
  }
  function save(s) { try { localStorage.setItem(KEY, JSON.stringify(s)); } catch (e) { /* ignore */ } }
  apply(get());
  document.addEventListener('DOMContentLoaded', function () { apply(get()); });
  document.addEventListener('htmx:afterSettle', function () { apply(get()); });
  document.addEventListener('input', function (ev) {
    var f = ev.target.closest && ev.target.closest('#fonts-form');
    if (!f || !ev.target.name) return;
    var s = get(); s[ev.target.name] = ev.target.value;
    save(s); apply(s);
  });
  // agent page zoom buttons (A- / size / A+): chatSize 10..32, 0 = default; syncs to other tabs and the drawer iframe via 'storage'
  document.addEventListener('click', function (ev) {
    var b = ev.target.closest && ev.target.closest('[data-chat-zoom]');
    if (!b) return;
    var s = get(), step = parseInt(b.getAttribute('data-chat-zoom'), 10) || 0, cur = parseInt(s.chatSize, 10) || 0;
    if (step === 0) cur = 0;
    else {
      if (!cur) { var ref = document.querySelector('.chat-text'); if (!ref) { try { ref = document.getElementById('vc-frame').contentDocument.querySelector('.chat-text'); } catch (e2) { ref = null; } } ref = ref || document.body; cur = Math.round(parseFloat(getComputedStyle(ref).fontSize)) || 15; }
      cur = Math.max(10, Math.min(32, cur + step));
    }
    s.chatSize = cur; save(s); apply(s);
  });
  // markdown viewer zoom (independent of the chat size): mdSize 10..32, 0 = default
  document.addEventListener('click', function (ev) {
    var b = ev.target.closest && ev.target.closest('[data-md-zoom]');
    if (!b) return;
    var s = get(), step = parseInt(b.getAttribute('data-md-zoom'), 10) || 0, cur = parseInt(s.mdSize, 10) || 0;
    if (step === 0) cur = 0;
    else {
      if (!cur) { var ref = document.querySelector('.mdpane-b .md, .pj-doc.md, .plan-doc.md'); ref = ref || document.body; cur = Math.round(parseFloat(getComputedStyle(ref).fontSize)) || 15; }
      cur = Math.max(10, Math.min(32, cur + step));
    }
    s.mdSize = cur; save(s); apply(s);
  });
  document.addEventListener('click', function (ev) {
    if (!(ev.target.closest && ev.target.closest('[data-fonts-reset]'))) return;
    try { localStorage.removeItem(KEY); } catch (e) { /* ignore */ }
    apply(get());
  });
  window.addEventListener('storage', function (ev) { if (ev.key === KEY || ev.key === null) apply(get()); });
})();
