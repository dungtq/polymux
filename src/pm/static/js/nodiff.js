/* Flicker-free polling for htmx fragments marked [data-nodiff].
 * - Periodic/SSE refreshes of a [data-nodiff] element are done here (fetch + If-None-Match), so
 *   htmx adds no request classes and an unchanged fragment (204 or equal HTML) touches nothing.
 * - Changed HTML is merged into the live DOM (keyed by data-key / id), never replaced wholesale:
 *   open <details>, scroll, focus and selection survive, no blank frame is painted.
 * - A child marked [data-keep] in the new HTML is left exactly as it is (it refreshes itself).
 * - A keyed node whose fresh copy sits under another parent (an agent row going Running -> Idle, Pinned) is moved
 *   there, not re-created: its open recap, <details> and [data-keep] children survive (no re-fetch, no flash).
 * Strict CSP: external file, no eval. */
(function () {
  'use strict';
  if (window.__pmNodiff) return;
  window.__pmNodiff = true;

  var etags = new WeakMap();
  var busy = new WeakMap();
  var pending = new WeakMap();  // an event came in while a fetch ran: fetch once more after it, never drop it

  function keyOf(n) {
    if (n.nodeType !== 1) return '';
    return n.getAttribute('data-key') || n.id || '';
  }
  function sameKind(a, b) {
    return a.nodeType === b.nodeType && a.nodeName === b.nodeName;
  }
  function syncAttrs(o, n) {
    var i, a;
    for (i = o.attributes.length - 1; i >= 0; i--) {
      a = o.attributes[i].name;
      if (!n.hasAttribute(a) && !(a === 'open' && o.nodeName === 'DETAILS') && a !== 'data-open') o.removeAttribute(a);  // data-open: expanded rows (rowx.js, srow.js) are owned by the user
    }
    for (i = 0; i < n.attributes.length; i++) {
      a = n.attributes[i];
      if (a.name === 'open' && o.nodeName === 'DETAILS') continue;  // the user owns open/closed
      if (o.getAttribute(a.name) !== a.value) o.setAttribute(a.name, a.value);
    }
  }
  /* every keyed node of the old tree (unique keys only), and the nodes a morph already placed */
  var pool = null, taken = null;
  function poolOf(root) {
    var m = new Map(), dup = new Set();
    root.querySelectorAll('[data-key], [id]').forEach(function (n) {
      var k = keyOf(n);
      if (m.has(k)) dup.add(k); else m.set(k, n);
    });
    dup.forEach(function (k) { m.delete(k); });
    return m;
  }
  function patch(o, n) {
    if (o.nodeType !== 1) {
      if (o.nodeValue !== n.nodeValue) o.nodeValue = n.nodeValue;
      return;
    }
    if (n.hasAttribute('data-keep')) return;
    if (o.isEqualNode(n)) return;
    syncAttrs(o, n);
    morph(o, n);
  }
  function morph(old, fresh) {
    var keyed = new Map(), used = taken || new Set(), cur, m, k, i, p;
    for (cur = old.firstChild; cur; cur = cur.nextSibling) {
      k = keyOf(cur);
      if (k) keyed.set(k, cur);
    }
    var kids = Array.prototype.slice.call(fresh.childNodes);
    cur = old.firstChild;
    for (i = 0; i < kids.length; i++) {
      var nk = kids[i];
      k = keyOf(nk);
      m = null;
      if (k) {
        m = keyed.get(k) || null;
        if (m && (used.has(m) || m.parentNode !== old || !sameKind(m, nk))) m = null;
        p = !m && pool && pool.get(k);  // moved here from another parent (or from a subtree removed above)
        if (p && !used.has(p) && sameKind(p, nk) && !p.contains(old)) m = p;
      } else if (cur && sameKind(cur, nk) && !keyOf(cur)) {
        m = cur;
      }
      if (m) {
        if (m !== cur) old.insertBefore(m, cur);
        used.add(m);
        patch(m, nk);
        cur = m.nextSibling;  // after the patch: it may have pulled the old next sibling into a nested list
      } else {
        old.insertBefore(nk, cur);
        used.add(nk);
      }
    }
    var rest = Array.prototype.slice.call(old.childNodes);
    rest.forEach(function (c) { if (!used.has(c)) old.removeChild(c); });
  }

  function apply(target, html) {
    var tpl = document.createElement('template');
    tpl.innerHTML = html;
    var y = window.scrollY, x = window.scrollX, active = document.activeElement;
    pool = poolOf(target); taken = new Set();
    try { morph(target, tpl.content); } finally { pool = taken = null; }
    if (window.htmx) window.htmx.process(target);
    if (active && active !== document.body && document.activeElement !== active && active.isConnected) {
      try { active.focus({ preventScroll: true }); } catch (e) { /* ignore */ }
    }
    if (window.scrollY !== y || window.scrollX !== x) window.scrollTo(x, y);
    target.dispatchEvent(new CustomEvent('nodiff:swapped', { bubbles: true }));
  }

  function pollUrl(el) {
    var p = el.querySelector(':scope > [data-poll]');
    return (p && p.getAttribute('data-poll')) || el.getAttribute('hx-get');
  }

  function refresh(el) {
    if (document.hidden) return null;
    if (busy.get(el)) { pending.set(el, true); return null; }
    var url = pollUrl(el);
    if (!url) return null;
    var h = { 'HX-Request': 'true', 'Accept': 'text/html' };
    if (el.id) h['HX-Target'] = el.id;
    var et = etags.get(el);
    if (et) h['If-None-Match'] = et;
    busy.set(el, true);
    return window.pm.fetch(url, { headers: h, credentials: 'same-origin', cache: 'no-store' }).then(function (r) {
      if (r.status !== 200 || r.redirected) return null;
      var tag = r.headers.get('ETag');
      return r.text().then(function (t) { if (tag) etags.set(el, tag); return t; });
    }).then(function (t) {
      if (t !== null && t !== undefined && el.isConnected) apply(el, t);
    }).catch(function () { /* offline: try again on the next tick */ })
      .then(function () { busy.set(el, false); if (pending.get(el)) { pending.delete(el); refresh(el); } });
  }

  // Sub-agents tab ([data-sa-poll="running_ms,idle_ms"]): own timer instead of htmx "every".
  // Polls only while the page and the tab are visible and the agent is not ended; fast while something
  // runs. A row's loaded detail ([data-keep]) is never morphed: a running open row re-fetches it in
  // place (no "Loading..." again), a row that just finished gets one last fetch, a finished one none.
  function saRefreshOpen(el) {
    el.querySelectorAll('details.sa-d[open]').forEach(function (d) {
      var w = d.querySelector(':scope > .sa-work');
      if (!w || !w.getAttribute('hx-get')) return;
      var run = d.getAttribute('data-run') === '1';
      if (run || w.getAttribute('data-ran') === '1') {
        if (refresh(w)) { if (run) w.setAttribute('data-ran', '1'); else w.removeAttribute('data-ran'); }
      }
    });
  }
  function saPoll(el) {
    var ms = (el.getAttribute('data-sa-poll') || '3000,15000').split(',');
    var timer = null;
    function tick() {
      clearTimeout(timer);
      if (!el.isConnected) return;
      var root = el.querySelector('#sa-root');
      if (root && root.hasAttribute('data-ended')) return;  // finished agent: nothing changes any more
      function later() {
        var r = el.querySelector('#sa-root');
        timer = setTimeout(tick, Number(ms[r && r.getAttribute('data-run') === '0' ? 1 : 0]) || 15000);
      }
      var p = (!document.hidden && el.offsetParent !== null) ? refresh(el) : null;
      if (p) p.then(function () { saRefreshOpen(el); later(); }); else later();
    }
    el.__saTick = tick;
    document.addEventListener('visibilitychange', function () { if (!document.hidden) tick(); });
    document.addEventListener('change', function (e) {  // the tab radios
      if (e.target && e.target.type === 'radio') setTimeout(tick, 0);
    });
    tick();
  }
  function saInit() {
    document.querySelectorAll('[data-sa-poll]').forEach(function (el) {
      if (!el.__saTick) saPoll(el);
    });
  }
  document.addEventListener('DOMContentLoaded', saInit);
  document.addEventListener('htmx:afterSettle', saInit);

  // htmx would add request classes and swap: take over the self-triggered refreshes.
  document.addEventListener('htmx:beforeRequest', function (e) {
    var el = e.detail && e.detail.elt;
    if (!el || !el.hasAttribute || !el.hasAttribute('data-nodiff')) return;
    var v = e.detail.requestConfig && e.detail.requestConfig.verb;
    if (v && v !== 'get') return;
    e.preventDefault();
    refresh(el);
  });

  // User-driven requests (chips) that target a [data-nodiff] element merge instead of replace.
  document.addEventListener('htmx:beforeSwap', function (e) {
    var d = e.detail, t = d && d.target;
    if (!t || !t.hasAttribute || !t.hasAttribute('data-nodiff')) return;
    if (!d.xhr || d.xhr.status !== 200 || d.isError) return;
    var sw = (d.requestConfig && d.requestConfig.elt && d.requestConfig.elt.getAttribute('hx-swap')) || 'innerHTML';
    if (sw !== 'innerHTML') return;
    d.shouldSwap = false;
    var tag = d.xhr.getResponseHeader('ETag');
    if (tag) etags.set(t, tag); else etags.delete(t);
    apply(t, d.serverResponse);
  });

  document.addEventListener('visibilitychange', function () {
    if (document.hidden) return;
    document.querySelectorAll('[data-nodiff][hx-trigger*="every"]').forEach(refresh);
  });
})();
