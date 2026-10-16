/* Expand/Collapse (rows past the first few) and the shared text filter (filter.js: pm.filter.match, html[data-pmq],
 * the `pm:filter` event) for the Ended sessions list and the ntfy panel.
 * One config per list; each list has its own <html> data attribute (data-<name>-all) and its own constructed
 * stylesheet, so the lists never affect each other. State lives on <html>, never on the fragment, so the live
 * morph (nodiff.js) and the panel's refresh cannot undo it. Strict CSP: external file, no eval.
 * /ntfy (scope all) is one list under a period tab bar (pm.tabs, saved as pm.ntfy.tab; mirrored on html data-ntfy-tab); a row shows when
 * its data-p holds the tab, it matches the filter text, and it is within the "Show" select (html data-ntfy-n:
 * 10/20/50/all) in the footer, which also holds the "showing X of N" line. The page's Mark all read form (#ntfy-markread) shows only while
 * the panel's data-unread is above 0. */
(function () {
  'use strict';
  var root = document.documentElement;
  var LISTS = [
    {name: 'ended', rows: '.ended-fold .nwrap[data-q]', hide: '.ended-fold .nwrap', toggle: '.ended-toggle'},
    {name: 'ntfy', rows: '.ntfy-list:not(.ntfy-pins) .ntfy-row[data-q]', pins: '.ntfy-pins .ntfy-row[data-q]', hide: '.ntfy-list .ntfy-row', pinhide: '.ntfy-pins .ntfy-row', more: '.ntfy-list .e-more',
     toggle: '.ntfy-toggle', tab: '.ntfy-tab', tablist: '.ntfy-tabs', select: '.ntfy-n', count: '.ntfy-shown', markread: '#ntfy-markread'}
  ];

  var cssEsc = window.pm.filter.cssEsc;  // from filter.js
  LISTS.forEach(function (L) {
    var sheet = new CSSStyleSheet();  // CSSOM, not a <style> element: the CSP forbids inline styles
    document.adoptedStyleSheets = document.adoptedStyleSheets.concat([sheet]);
    var attrAll = 'data-' + L.name + '-all';

    var NKEY = 'pm.ntfy.n';
    function tabRules() {  // /ntfy: period tab, text filter and the Show cap; updates the count line
      var rules = [], list = document.querySelector(L.tablist), sels = document.querySelectorAll(L.select), sel = sels[0], n = 0;
      var cur = list && window.pm.tabs.restore(list);  // pm.tabs: this page's last choice, else the saved tab (pm.ntfy.tab), else Today
      if (!cur) return rules;
      var f = cur.getAttribute('data-tab'), total = 0, shown = 0;
      root.setAttribute('data-ntfy-tab', f);
      if (sel) {
        var want = root.getAttribute('data-ntfy-n');
        if (!want) { try { want = localStorage.getItem(NKEY); } catch (e) { want = null; } }
        sels.forEach(function (s) {  // every Show select (filter row + footer) follows the one choice
          if (want && s.querySelector('option[value="' + cssEsc(want) + '"]') && s.value !== want && document.activeElement !== s) s.value = want;
        });
        n = sel.value === 'all' ? 0 : parseInt(sel.value, 10) || 0;
      }
      document.querySelectorAll(L.rows).forEach(function (r) {
        var ok = (' ' + r.getAttribute('data-p') + ' ').indexOf(' ' + f + ' ') >= 0 && pm.filter.match(r);
        if (ok) total++;
        if (ok && n && shown >= n) ok = false;
        if (ok) shown++;
        else rules.push(L.hide + '[data-key="' + cssEsc(r.getAttribute('data-key')) + '"]{display:none}');
      });
      document.querySelectorAll(L.count).forEach(function (c) { c.textContent = 'showing ' + shown + ' of ' + total; });
      return rules;
    }
    function pinRules() {  // pinned rows: only the text filter, not the tab, the Show cap or the count
      var rules = [];
      if (L.pins) document.querySelectorAll(L.pins).forEach(function (r) {
        if (!pm.filter.match(r)) rules.push(L.pinhide + '[data-key="' + cssEsc(r.getAttribute('data-key')) + '"]{display:none}');
      });
      return rules;
    }
    function apply() {
      if (L.markread) {
        var inner = document.querySelector('.ntfy-inner[data-unread]');
        document.querySelectorAll(L.markread).forEach(function (f) { if (inner) f.hidden = !(parseInt(inner.getAttribute('data-unread'), 10) > 0); });
      }
      var tabbed = !!(L.tab && document.querySelector(L.tab));
      var rules = (tabbed ? tabRules() : []).concat(pinRules());
      if (!tabbed && pm.filter.q()) document.querySelectorAll(L.rows).forEach(function (r) {
        if (!pm.filter.match(r)) rules.push(L.hide + '[data-key="' + cssEsc(r.getAttribute('data-key')) + '"]{display:none}');
      });
      sheet.replaceSync(rules.join('\n'));
      var all = root.hasAttribute(attrAll);
      document.querySelectorAll(L.toggle).forEach(function (b) {
        b.textContent = all ? 'Collapse' : 'Expand';
        b.setAttribute('aria-pressed', all ? 'true' : 'false');
        if (L.more) b.hidden = !document.querySelector(L.more);  // nothing past the default: no toggle
      });
    }
    if (L.select) {
      document.addEventListener('change', function (ev) {
        if (!ev.target.closest || !ev.target.closest(L.select)) return;
        root.setAttribute('data-ntfy-n', ev.target.value);
        document.querySelectorAll(L.select).forEach(function (s) { s.value = ev.target.value; });
        try { localStorage.setItem(NKEY, ev.target.value); } catch (e) { /* private mode */ }
        apply();
      });
    }
    if (L.tablist) {  // a click, arrow key or live refresh of the period tabs (pm.tabs selects and saves it)
      document.addEventListener('pm:tab', function (ev) {
        if (ev.target.matches && ev.target.matches(L.tablist)) apply();
      });
    }
    document.addEventListener('click', function (ev) {
      var b = ev.target.closest && ev.target.closest(L.toggle);
      if (!b) return;
      if (root.hasAttribute(attrAll)) root.removeAttribute(attrAll); else root.setAttribute(attrAll, '1');
      apply();
    });
    document.addEventListener('nodiff:swapped', apply);
    document.addEventListener('htmx:afterSwap', apply);
    document.addEventListener('pm:filter', apply);
    apply();
  });
})();
