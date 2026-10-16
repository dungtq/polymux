/* pm.tabs: the one tab bar behaviour (look: css/tabs.css). Loaded right after core.js (uses pm.store).
 *
 * A tab bar is [role=tablist] holding [role=tab] elements (button or link). The current tab has aria-selected="true";
 * the others "false". Roving tabindex: only the current tab is in the Tab order (labels of radio tabs are skipped).
 *   pm.tabs.select(tab, {silent, persist}) marks tab current in its list, shows the panels named by aria-controls and hides
 *     the others, keeps it in view in a scrolling row, remembers it (list data-tabs-key, unless persist === false) and fires
 *     `pm:tab` on the list (detail {tab, value}; not with silent). Returns the tab.
 *   pm.tabs.restore(list) re-selects the remembered tab of a keyed list (this page's last choice, then pm.store), else the
 *     one marked in the markup, else the first visible one; silent, returns it. Use it after the list was re-rendered.
 *   pm.tabs.value(tab) = its data-tab, else its id.
 * Lists with data-tabs-key are fully handled here: a click selects (and persists), and every live refresh (nodiff:swapped,
 * htmx:afterSettle) restores them and fires `pm:tab` (detail.restored) so the page can re-apply its side effects.
 * Other lists (project doc tabs, the chat aside) keep their own click handler and call select() themselves.
 * Arrow Left/Right, Home and End move between the visible tabs of any tablist and click the new one.
 * Radio tabs (<label for> of a hidden radio, switching panels in CSS alone): aria-selected follows the radio on change. */
(function () {
  'use strict';
  var pm = window.pm = window.pm || {}, d = document, mem = {};
  var TAB = '[role=tab]';

  function tabsOf(list) {
    return Array.prototype.filter.call(list.querySelectorAll(TAB), function (t) { return t.closest('[role=tablist]') === list; });
  }
  function shown(t) { return !t.hidden && t.getClientRects().length > 0; }
  function value(t) { return t.getAttribute('data-tab') || t.id || ''; }
  function fire(list, t, extra) {
    var det = { tab: t, value: value(t) };
    if (extra) Object.keys(extra).forEach(function (k) { det[k] = extra[k]; });
    list.dispatchEvent(new CustomEvent('pm:tab', { bubbles: true, detail: det }));
  }
  function panel(id) { return id ? d.getElementById(id) : null; }

  function select(t, opts) {
    var o = opts || {}, list = t && t.closest('[role=tablist]');
    if (!list) return null;
    var was = t.getAttribute('aria-selected') === 'true';
    tabsOf(list).forEach(function (x) {
      var on = x === t;
      x.setAttribute('aria-selected', on ? 'true' : 'false');
      if (x.tagName !== 'LABEL') x.tabIndex = on ? 0 : -1;
      var p = panel(x.getAttribute('aria-controls'));
      if (p) p.hidden = !on;
    });
    var key = list.getAttribute('data-tabs-key');
    if (key && o.persist !== false) { mem[key] = value(t); pm.store.set(key, value(t)); }
    /* phones: a tab row that scrolls sideways centres the newly selected tab */
    if (!was && list.scrollWidth > list.clientWidth) list.scrollLeft = Math.max(0, t.offsetLeft - list.offsetLeft - (list.clientWidth - t.offsetWidth) / 2);
    if (!o.silent) fire(list, t);
    return t;
  }

  function restore(list) {
    var all = tabsOf(list);
    if (!all.length) return null;
    var key = list.getAttribute('data-tabs-key'), want = key ? (mem[key] || pm.store.get(key, '')) : '', t = null;
    if (want) t = all.filter(function (x) { return value(x) === want && !x.hidden; })[0] || null;
    if (!t) t = all.filter(function (x) { return x.getAttribute('aria-selected') === 'true' && !x.hidden; })[0] || null;
    if (!t) t = all.filter(function (x) { return !x.hidden; })[0] || null;
    return t ? select(t, { silent: true, persist: false }) : null;
  }

  function radioSync(list) {   /* radio tabs: aria-selected mirrors :checked */
    tabsOf(list).forEach(function (l) {
      var r = l.htmlFor && d.getElementById(l.htmlFor);
      if (r) l.setAttribute('aria-selected', r.checked ? 'true' : 'false');
    });
  }

  function refresh() {
    d.querySelectorAll('[role=tablist][data-tabs-key]').forEach(function (list) {
      var t = restore(list);
      if (t) fire(list, t, { restored: true });
    });
    d.querySelectorAll('[role=tablist]').forEach(function (list) {
      if (list.querySelector('label[for]' + TAB)) { radioSync(list); return; }
      if (list.hasAttribute('data-tabs-key')) return;
      var all = tabsOf(list), cur = all.filter(function (x) { return x.getAttribute('aria-selected') === 'true'; })[0] || all[0];
      all.forEach(function (x) { x.tabIndex = x === cur ? 0 : -1; });
    });
  }

  d.addEventListener('click', function (e) {
    var t = e.target.closest && e.target.closest(TAB), list = t && t.closest('[role=tablist][data-tabs-key]');
    if (!list) return;
    if (t.tagName === 'A') e.preventDefault();
    select(t);
  });
  d.addEventListener('keydown', function (e) {
    var k = e.key;
    if (k !== 'ArrowLeft' && k !== 'ArrowRight' && k !== 'Home' && k !== 'End') return;
    if (e.altKey || e.ctrlKey || e.metaKey || e.shiftKey) return;
    var t = e.target.closest && e.target.closest(TAB), list = t && t.closest('[role=tablist]');
    if (!list || t.tagName === 'LABEL') return;
    var all = tabsOf(list).filter(shown), i = all.indexOf(t), n;
    if (i < 0 || all.length < 2) return;
    n = k === 'Home' ? all[0] : k === 'End' ? all[all.length - 1]
      : all[(i + (k === 'ArrowRight' ? 1 : all.length - 1)) % all.length];
    e.preventDefault();
    n.focus();
    n.click();
  });
  d.addEventListener('change', function (e) {
    var r = e.target;
    if (!r || r.type !== 'radio' || !r.id) return;
    d.querySelectorAll('[role=tablist]').forEach(function (list) {
      if (list.querySelector('label[for="' + CSS.escape(r.id) + '"]')) radioSync(list);
    });
  });
  d.addEventListener('nodiff:swapped', refresh);
  d.addEventListener('htmx:afterSettle', refresh);

  pm.tabs = { select: select, restore: restore, value: value, refresh: refresh };
  refresh();
})();
