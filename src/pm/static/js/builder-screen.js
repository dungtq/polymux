/* Builder preview: screen size presets and zoom, like the browser's device mode. A preset is laid out at its real
 * CSS width x height and scaled by the zoom ("Fit" = as large as fits the stage, never above 100%); a bigger zoom
 * scrolls the stage. CSS reads --scr-w/-h/-s from data-size. Ctrl/Cmd + wheel over the stage steps the zoom.
 * Static file (CSP script-src 'self'): no inline script, no eval, no native popups. Needs builder.js (load/store). */
(function () {
  'use strict';
  var root = document.getElementById('builder'), b = window.pm && window.pm.builder;
  if (!root || !b) return;
  var $ = function (id) { return document.getElementById(id); };
  var stage = $('bld-stage'), sizeSel = $('bld-size'), zoomSel = $('bld-zoom');
  var DK = 'pm-builder-device', ZK = 'pm-builder-zoom';
  var OLD = { desk: 'fit', tab: 'tab', phone: 'ip17pm' };  // values stored by the old three-button toggle
  var STEPS = [25, 50, 75, 100, 125, 150, 200], fitOpt = zoomSel.querySelector('option[value="fit"]');

  function applySize() {
    var o = sizeSel.options[sizeSel.selectedIndex], w = +o.dataset.w, h = +o.dataset.h;
    if (!w || !h) {
      stage.removeAttribute('data-size');
      ['--scr-w', '--scr-h', '--scr-s'].forEach(function (k) { stage.style.removeProperty(k); });
      zoomSel.hidden = true;
      return;
    }
    var aw = stage.clientWidth - 32, ah = stage.clientHeight - 32;
    if (aw <= 0 || ah <= 0) return;  // pane hidden (phone Chat tab): the observer calls again when it shows
    var fit = Math.min(1, aw / w, ah / h), k = zoomSel.value === 'fit' ? fit : +zoomSel.value / 100;
    stage.dataset.size = o.value;
    stage.style.setProperty('--scr-w', w + 'px');
    stage.style.setProperty('--scr-h', h + 'px');
    stage.style.setProperty('--scr-s', k.toFixed(4));
    fitOpt.textContent = 'Fit ' + Math.round(fit * 100) + '%';
    zoomSel.hidden = false;
  }
  function pick(sel, key, val) {
    if (val && sel.querySelector('option[value="' + val + '"]')) sel.value = val;
    sel.addEventListener('change', function () { b.store(key, sel.value); applySize(); });
  }
  var sd = b.load(DK);
  pick(sizeSel, DK, OLD[sd] || sd);
  pick(zoomSel, ZK, b.load(ZK));
  if (window.ResizeObserver) new ResizeObserver(applySize).observe(stage);  // divider drag, window, pane shown
  applySize();

  /* Ctrl/Cmd + wheel (also a trackpad pinch) steps through the presets, from the current scale */
  var lastStep = 0;
  stage.addEventListener('wheel', function (e) {
    if (!(e.ctrlKey || e.metaKey) || !stage.dataset.size) return;
    e.preventDefault();
    var now = Date.now();
    if (now - lastStep < 90) return;
    lastStep = now;
    var cur = Math.round((parseFloat(stage.style.getPropertyValue('--scr-s')) || 1) * 100), next = null;
    if (e.deltaY < 0) { for (var i = 0; i < STEPS.length; i++) if (STEPS[i] > cur) { next = STEPS[i]; break; } }
    else { for (var j = STEPS.length - 1; j >= 0; j--) if (STEPS[j] < cur) { next = STEPS[j]; break; } }
    if (next === null) return;
    zoomSel.value = String(next);
    b.store(ZK, zoomSel.value);
    applySize();
  }, { passive: false });
  /* the iframe swallows wheel events: while Ctrl/Cmd is held the wheel goes through it to the stage */
  function ctrl(e) { root.classList.toggle('bld-ctrl', !!(e.ctrlKey || e.metaKey)); }
  document.addEventListener('keydown', ctrl);
  document.addEventListener('keyup', ctrl);
  window.addEventListener('blur', function () { root.classList.remove('bld-ctrl'); });
})();
