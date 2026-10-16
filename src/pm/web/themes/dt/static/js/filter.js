/* dt theme (pm v7) filter box (DESIGN.md §6.5; contract docs/themes/dt/PARTS.md §5): the ONE
   implementation for page lists (the sidebar box is sidebar.js's own). Markup: templates/parts/_filter.html.
   Globals: pm7.filter = {apply(input), clear(input), q(name?) -> current text, match(text, q) -> bool}.
   Hooks: div.p-fbox > input[type=search][name=q][data-p-filter=<name>] + button[data-p-fclr].
     Client mode: data-p-filter-rows="<selector>": rows [data-p-q] (lower-case text) inside it are hidden unless they
       contain every typed word; a .p-gg group with no row left hides; [data-p-fnone] shows when nothing matches.
       Re-applied after every htmx swap. Event pm7:filter on the input {name, q, shown, total}.
     Server mode: htmx attributes on the input (parts/_filter.html). On its requests the page URL's other query
       parameters (segment, sort, group) are kept; segment / sort / show-all links and controls-row elements that load
       with htmx (inside .p-seg, .p-gh, .p-foot, .p-tools) send the box's current q.
     Swaps: when a swap replaces the focused box, the new one gets the typed text, the caret and the page scroll back.
   Keys: / is keys.js, Esc (clear, then leave the field) is layers.js: this file has no keydown listener. */
(function () {
  'use strict';
  const pm7 = window.pm7, d = document;
  const BOX = 'input[data-p-filter]';

  function fold(s) { return String(s || '').trim().toLowerCase().replace(/\s+/g, ' '); }
  function match(text, q) {
    const words = fold(q).split(' ').filter(Boolean), t = String(text || '');
    return words.every((w) => t.indexOf(w) >= 0);
  }
  function box(name) {
    return name ? d.querySelector('input[data-p-filter="' + CSS.escape(name) + '"]') : d.querySelector('main ' + BOX);
  }
  function q(name) { const i = box(name); return i ? i.value : ''; }

  /* ---------- client mode ---------- */
  function apply(input) {
    const sel = input.getAttribute('data-p-filter-rows'), root = sel ? d.querySelector(sel) : null;
    if (!root) return;
    const t = fold(input.value);
    let shown = 0, total = 0;
    pm7.$$('[data-p-q]', root).forEach((r) => {
      const ok = !t || match(r.getAttribute('data-p-q'), t);
      total++;
      if (ok) shown++;
      if (r.hidden === ok) r.hidden = !ok;
    });
    pm7.$$('.p-gg', root).forEach((g) => {
      const off = !!t && !!g.querySelector('[data-p-q]') && !g.querySelector('[data-p-q]:not([hidden])');
      if (g.hidden !== off) g.hidden = off;
    });
    pm7.$$('[data-p-fnone]', root).forEach((n) => { n.hidden = !(t && total && !shown); });
    input.dispatchEvent(new CustomEvent('pm7:filter', { bubbles: true,
      detail: { name: input.getAttribute('data-p-filter'), q: t, shown: shown, total: total } }));
  }
  function applyAll() { pm7.$$(BOX + '[data-p-filter-rows]').forEach(apply); }

  function clear(input) {
    if (!input) return;
    input.value = '';
    input.dispatchEvent(new Event('input', { bubbles: true }));  // htmx "input changed" and the client filter
    input.focus();
  }

  d.addEventListener('input', (e) => {
    const i = e.target && e.target.closest ? e.target.closest(BOX) : null;
    if (i && i.hasAttribute('data-p-filter-rows')) apply(i);
  });
  d.addEventListener('click', (e) => {
    const b = e.target && e.target.closest ? e.target.closest('[data-p-fclr]') : null;
    if (!b) return;
    e.preventDefault();
    clear(pm7.$(BOX, b.closest('.p-fbox') || d));
  });

  /* ---------- server mode: keep the page's other parameters, carry q to segment / sort links ---------- */
  function has(p, k) { try { return !!p && k in p; } catch (err) { return false; } }  // htmx 2: a FormData proxy
  d.addEventListener('htmx:configRequest', (e) => {
    const x = e.detail || {}, elt = x.elt;
    if (!elt || !elt.closest || String(x.verb || '').toLowerCase() !== 'get' || !x.path) return;
    const u = new URL(x.path, window.location.href);
    if (u.origin !== window.location.origin) return;
    if (elt.matches(BOX)) {
      new URLSearchParams(window.location.search).forEach((v, k) => {
        if (k !== 'q' && !u.searchParams.has(k) && !has(x.parameters, k)) u.searchParams.append(k, v);
      });
      u.searchParams.delete('q');                    // the box's own name=q parameter carries it
    } else if (elt.closest('.p-seg, .p-gh, .p-foot, .p-tools')) {
      const v = fold(q());
      if (v) u.searchParams.set('q', v); else u.searchParams.delete('q');
    } else {
      return;
    }
    x.path = u.pathname + u.search;
  });

  /* ---------- swaps: the box keeps text, caret and page scroll; client filters apply again ---------- */
  let saved = null;
  d.addEventListener('htmx:beforeSwap', () => {
    const a = d.activeElement, m = d.getElementById('main');
    saved = a && a.matches && a.matches(BOX) ? { name: a.getAttribute('data-p-filter'), value: a.value,
      s: a.selectionStart, e: a.selectionEnd, dir: a.selectionDirection, left: a.scrollLeft,
      top: m ? m.scrollTop : 0 } : null;
  });
  d.addEventListener('htmx:afterSettle', () => {
    applyAll();
    const s = saved;
    saved = null;
    if (!s) return;
    const a = d.activeElement;
    if (a && a.matches && a.matches(BOX) && a.getAttribute('data-p-filter') === s.name) return;  // never lost
    const i = box(s.name);
    if (!i || (a && a !== d.body && d.contains(a))) return;  // focus moved on purpose
    if (i.value !== s.value) { i.value = s.value; if (i.hasAttribute('data-p-filter-rows')) apply(i); }
    i.focus({ preventScroll: true });
    try { i.setSelectionRange(s.s, s.e, s.dir || 'none'); } catch (err) { /* not a text field */ }
    i.scrollLeft = s.left;
    const m = d.getElementById('main');
    if (m && m.scrollTop !== s.top) m.scrollTop = s.top;
  });

  pm7.filter = { apply: apply, clear: clear, q: q, match: match };
  pm7.ready(() => pm7.$$(BOX + '[data-p-filter-rows]').forEach((i) => { if (i.value) apply(i); }));
})();
