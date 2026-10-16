/* Project page: tmux | Stack | VM tabs above the sessions list. Selection, arrow keys, the remembered tab (data-tabs-key
   pm.ptab) and the re-apply after every live refresh are pm.tabs (tabs.js); this file only runs the page's side effects on
   `pm:tab`: the tmux section actions show with the tmux tab, and the VM panel is loaded on its first activation (htmx,
   event pm:vmload) and again on vms-changed while it is loaded. */
(function () {
  'use strict';
  var d = document, loaded = false;
  function vmPanel() { return d.querySelector('[data-ptab-panel="vm"]'); }
  function load() {
    var p = vmPanel();
    if (p && window.htmx) { window.htmx.trigger(p, 'pm:vmload'); loaded = true; }
  }
  function effects(t) {
    var name = (t && t.getAttribute('data-ptab')) || 'tmux';
    d.querySelectorAll('[data-slot="tmux-section-actions"]').forEach(function (a) { a.hidden = name !== 'tmux'; });
    if (name === 'vm' && !loaded) load();
  }
  d.addEventListener('pm:tab', function (e) { if (e.target.matches && e.target.matches('[data-ptabs]')) effects(e.detail.tab); });
  d.body.addEventListener('vms-changed', function () { var p = vmPanel(); if (loaded && p && !p.hidden) load(); });
  var list = d.querySelector('[data-ptabs]');
  if (list) effects(window.pm.tabs.restore(list));
})();
