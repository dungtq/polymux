/* One shared list filter box (window.pm.filter). Every input[data-filter] (one per page) edits the same query:
 * the inputs stay in sync, html[data-pmq] is set while the query is non-empty, and `pm:filter` fires on document
 * after each change. This file hides non-matching [data-frow][data-q] rows (every list page and the project page's
 * Agents/Ended tables) through one adopted stylesheet; ended.js does the ntfy rows and the Ended fold via pm.filter.match(). A page
 * with a form[data-filter-live][data-filter-target="#id"] around the box also sends the query to the server
 * (debounced): the target's hx-get carries q; a page may have several such forms (Audit: the log and Client errors), and
 * only the one whose action is the page's path also updates the address bar, the sort / [data-qlink] links and the
 * bottom Show form. A <select> without data-nav in such a form re-fetches too.
 * A box with data-filter-scope="<selector>" (the sidebar) is separate: its own query, only the [data-q] rows inside
 * that element (hidden attribute; [data-fnone] shows when none match), never the page's query or the server.
 * Esc or the x clears, Enter only blurs (no page reload). Strict CSP: external file, CSSOM sheet, no eval. */
(function () {
  'use strict';
  var root = document.documentElement;
  var INPUT = 'input[data-filter]:not([data-filter-scope])', ANY = 'input[data-filter]';
  var raw = '';
  var sheet = new CSSStyleSheet();  // CSSOM, not a <style> element: the CSP forbids inline styles
  document.adoptedStyleSheets = document.adoptedStyleSheets.concat([sheet]);
  var lastRules = null, openedFor = '';

  function fold(s) { return s.trim().toLowerCase().replace(/\s+/g, ' '); }  // same folding as the server's q
  function q() { return fold(raw); }
  function hit(el, t) { return !t || (el.getAttribute('data-q') || '').indexOf(t) >= 0; }
  function match(el) { return hit(el, q()); }
  function cssEsc(s) { return s.replace(/["\\]/g, '\\$&'); }
  function inputs() { return document.querySelectorAll(INPUT); }

  function syncInputs(except) {
    inputs().forEach(function (i) {
      if (i !== except && i.value !== raw && document.activeElement !== i) i.value = raw;
    });
  }

  function openIdle() {  // a match inside the folded Idle list: open it, once per query
    var t = q();
    if (!t) { openedFor = ''; return; }
    if (t === openedFor) return;
    var d = document.querySelector('details.idle-fold');
    if (!d || !Array.prototype.some.call(d.querySelectorAll('[data-frow][data-q]'), match)) return;
    openedFor = t;
    if (!d.open) d.open = true;
  }

  function hideRows() {
    var t = q(), rules = [];
    if (t) document.querySelectorAll('[data-frow][data-q]').forEach(function (r) {
      if (!match(r)) rules.push('[data-frow][data-key="' + cssEsc(r.getAttribute('data-key') || '') + '"]{display:none}');
    });
    var css = rules.join('\n');
    if (css !== lastRules) { lastRules = css; sheet.replaceSync(css); }
    if (t) root.setAttribute('data-pmq', '1'); else root.removeAttribute('data-pmq');
    openIdle();
    emptySections(t);
  }

  /* A list whose rows are all filtered out hides (its column header band goes with it); a page with no visible row
   * at all shows one "No matches" line under the filter bar. A row without data-frow (the ?open= session) counts as visible. */
  function emptySections(t) {
    var any = false, total = 0;
    document.querySelectorAll('.list, .nlist, .ntable').forEach(function (c) {
      var rows = c.querySelectorAll('[data-frow], .srowwrap');
      var vis = !t || !rows.length || Array.prototype.some.call(rows, function (r) { return !r.hasAttribute('data-frow') || match(r); });
      if (rows.length) { total += rows.length; if (vis) any = true; }
      if (vis) c.removeAttribute('data-fempty'); else c.setAttribute('data-fempty', '');
    });
    var bar = document.querySelector('.filterbar'), note = document.querySelector('.fb-none');
    if (!bar) return;
    if (!note) { note = document.createElement('p'); note.className = 'fb-none muted'; note.textContent = 'No matches'; note.hidden = true; bar.insertAdjacentElement('afterend', note); }
    note.hidden = !(t && total && !any);
  }

  window.pm = window.pm || {};
  window.pm.filter = {q: q, match: match, cssEsc: cssEsc};

  /* ---- scoped box (sidebar): own query, rows inside the scope only ---- */
  function scoped(i) {
    var root = document.querySelector(i.getAttribute('data-filter-scope'));
    if (!root) return;
    var t = fold(i.value), n = 0;
    root.querySelectorAll('[data-q]').forEach(function (r) { var ok = hit(r, t); r.hidden = !ok; if (ok) n++; });
    root.querySelectorAll('[data-fnone]').forEach(function (p) { p.hidden = !t || n > 0; });
  }

  /* ---- server-side q: a form[data-filter-live][data-filter-target] holds the box; the query goes to the server so
   * the counts, the poll URL, the sort links and the bottom Show form follow it. Without such a form the page is
   * client-only. Forms are looked up lazily: a list swap replaces them. ---- */
  function liveForms() { return document.querySelectorAll('form[data-filter-live]'); }
  function liveTarget(form) { return form && form.dataset.filterTarget ? document.querySelector(form.dataset.filterTarget) : null; }
  function ownsPage(form) { return (form.getAttribute('action') || '/') === window.location.pathname; }

  function liveUrl(form) {
    var p = new URLSearchParams();
    new FormData(form).forEach(function (v, k) { if (k !== 'q' && v !== '') p.append(k, v); });
    if (raw.trim()) p.set('q', raw.trim());
    var s = p.toString();
    return (form.getAttribute('action') || '/') + (s ? '?' + s : '');
  }
  function setQ(href, t) {  // href with q replaced (or removed)
    var u = new URL(href, window.location.href);
    if (t) u.searchParams.set('q', t); else u.searchParams.delete('q');
    return u.pathname + u.search + u.hash;
  }
  function freshLinks(action) {  // sort links, [data-qlink] links and the bottom Show form must not carry a stale q
    var t = raw.trim();
    document.querySelectorAll('a.hcell[href], a[data-qlink][href]').forEach(function (a) {
      var h = a.getAttribute('href');
      var n = h && h.charAt(0) !== '#' ? setQ(h, t) : h;
      if (n !== h) a.setAttribute('href', n);
    });
    document.querySelectorAll('form.showbar').forEach(function (f) {
      if ((f.getAttribute('action') || '/') !== action) return;
      var i = f.querySelector('input[type=hidden][name=q]');
      if (!i && t) {
        i = document.createElement('input');
        i.type = 'hidden'; i.name = 'q';
        f.insertBefore(i, f.firstChild);
      }
      if (i) { if (t) i.value = t; else i.remove(); }
    });
  }
  function ping() {
    liveForms().forEach(function (f) {
      var box = liveTarget(f);
      if (box && window.htmx) window.htmx.trigger(box, 'pm:refresh');
    });
  }
  var sendLive = window.pm.debounce ? window.pm.debounce(function () {
    var any = false;
    liveForms().forEach(function (form) {
      var box = liveTarget(form);
      if (!box) return;
      var u = liveUrl(form);
      if (box.getAttribute('hx-get') !== u) {
        box.setAttribute('hx-get', u);
        if (window.htmx) window.htmx.process(box);  // htmx keeps the URL it read at init; [data-nodiff] lists read it live
      }
      any = true;
      if (!ownsPage(form)) return;
      try { history.replaceState(null, '', u); } catch (e) { /* ignore */ }
      freshLinks(form.getAttribute('action') || '/');
    });
    if (!any) return;
    ping();
    setTimeout(ping, 600);  // in case a poll was in flight and skipped the first one
  }, 150) : function () {};

  /* ---- events ---- */
  function change(v, from) {
    raw = v;
    syncInputs(from);
    hideRows();
    if (liveForms().length) sendLive();
    document.dispatchEvent(new CustomEvent('pm:filter'));
  }
  function edit(i, v) {  // typed, cleared or Esc in any box: a scoped one stays out of the page's query
    if (i.hasAttribute('data-filter-scope')) { if (v !== null) i.value = v; scoped(i); }
    else { if (v !== null) i.value = v; change(i.value, i); }
  }

  document.addEventListener('input', function (ev) {
    var i = ev.target.closest && ev.target.closest(ANY);
    if (i) edit(i, null);
  });
  document.addEventListener('change', function (ev) {  // a plain select in a live form (Client errors kind) re-fetches
    var s = ev.target.closest && ev.target.closest('form[data-filter-live] select:not([data-nav])');
    if (s) sendLive();
  });
  document.addEventListener('keydown', function (ev) {
    var i = ev.target.closest && ev.target.closest(ANY);
    if (!i) return;
    if (ev.key === 'Enter') { ev.preventDefault(); i.blur(); }
    else if (ev.key === 'Escape' && i.value) { ev.preventDefault(); edit(i, ''); }
  });
  document.addEventListener('click', function (ev) {
    var x = ev.target.closest && ev.target.closest('.fbox-x');
    if (!x) return;
    var i = x.previousElementSibling;
    if (!i || !i.matches(ANY)) return;
    edit(i, '');
    i.focus();
  });
  document.addEventListener('submit', function (ev) {  // Enter in the box must not reload the page
    if (ev.target.closest && ev.target.closest('form[data-filter-live]')) ev.preventDefault();
  });

  function again() { syncInputs(null); hideRows(); }  // polled panels come back with the right q already
  document.addEventListener('nodiff:swapped', again);
  document.addEventListener('htmx:afterSwap', again);
  document.addEventListener('pm:filter', hideRows);

  /* initial state: the server-rendered value (?q=) */
  inputs().forEach(function (i) { if (!raw && i.value) raw = i.value; });
  syncInputs(null);
  hideRows();
  document.querySelectorAll('input[data-filter][data-filter-scope]').forEach(function (i) { if (i.value) scoped(i); });
})();
