/* dt theme (pm v7) small shared parts (contract PARTS.md): flat tabs (§3), segments (§4), switch (§8) and the
   ⋯ menu trigger (§9). Markup: templates/parts/_tabs.html, _seg.html, _switch.html, _menu.html.
   Globals: pm7.menus = {open(trigger), items(trigger) -> pm7.menu items, run(trigger, item id) -> bool,
     exec(opener, item) -> bool (runs one item, e.g. a page button's data: Settings)}.
   Flat tabs (.p-tabs[role=tablist]): ←/→/Home/End move focus with a roving tabindex (capture phase, stopped: classic
     tabs.js, loaded for un-ported content, would click every tab); Enter follows a link tab; a button tab
     (aria-controls) is selected on click / Enter / Space: aria-selected, its tabpanel shown, the others hidden, event
     pm7:tab {key} on the tablist.
   Segments (.p-seg): a link that loads with htmx (hx-get) takes aria-current at once (it is a filter choice, not a
     result). Scrolling strips (.p-tabs, .p-seg; phones) centre their selected item after the layout settles, after
     swaps and on a tab choice (unless the user scrolled the strip), never leaving a sliver of an item (e.g. only its
     count) at the start edge, and carry .ovf / .beg / .end for the "more" fades at either edge.
   Switch (button.sw[role=switch]): a click flips aria-checked and the sibling hidden input ("1" / "0"), then a
     bubbling `change` fires from that input (capture phase, so an hx-* on the switch sees the new state).
   ⋯ menu ([data-menu] = JSON items, parts/_menu.html): a click opens pm7.menu (layers.js: one stack, Esc, outside
     click, phone sheet); items run as described in parts/_menu.html; a post sends Accept: application/json and
     counts as failed on an HTTP error or a JSON {ok: false} (pm7.reply): it toasts the server's message (pm7.fail,
     core.js) under the item's label; on success the toast is the server's notice, else the item's done; a JSON
     redirect (a same-origin path: Log out) is followed. */
(function () {
  'use strict';
  const pm7 = window.pm7, d = document;

  /* ---------- flat tabs ---------- */
  const TAB = '.p-tabs[role=tablist] [role=tab]';
  const tabsOf = (l) => pm7.$$('[role=tab]', l).filter((t) => t.closest('[role=tablist]') === l);
  function select(t) {
    const l = t.closest('[role=tablist]');
    tabsOf(l).forEach((x) => {
      const on = x === t, p = x.getAttribute('aria-controls');
      x.setAttribute('aria-selected', on ? 'true' : 'false');
      x.tabIndex = on ? 0 : -1;
      const panel = p ? d.getElementById(p) : null;
      if (panel) panel.hidden = !on;
    });
    l.dispatchEvent(new CustomEvent('pm7:tab', { bubbles: true, detail: { key: t.getAttribute('data-p-tab') } }));
  }
  d.addEventListener('keydown', (e) => {
    const k = e.key;
    if (k !== 'ArrowLeft' && k !== 'ArrowRight' && k !== 'Home' && k !== 'End') return;
    const t = e.target && e.target.closest ? e.target.closest(TAB) : null;
    if (!t) return;
    e.stopImmediatePropagation();
    if (e.altKey || e.ctrlKey || e.metaKey || e.shiftKey) return;
    const all = tabsOf(t.closest('[role=tablist]')).filter((x) => x.getClientRects().length);
    const i = all.indexOf(t);
    if (i < 0 || all.length < 2) return;
    const n = k === 'Home' ? all[0] : k === 'End' ? all[all.length - 1]
      : all[(i + (k === 'ArrowRight' ? 1 : all.length - 1)) % all.length];
    e.preventDefault();
    all.forEach((x) => { x.tabIndex = x === n ? 0 : -1; });
    n.focus();
    n.scrollIntoView({ block: 'nearest', inline: 'nearest' });
  }, true);

  /* ---------- strips that scroll sideways (.p-tabs, .p-seg): .ovf while they overflow, .beg / .end when scrolled to
     the start / end (the fades, css/parts.css); the selected item is centred once the layout has settled (fonts
     loaded), again after a swap or a resize while the user has not scrolled the strip, and when a tab is chosen. An
     item cut at the start edge keeps at least half of itself in view (else it is scrolled out), so a label is never
     reduced to its count there (when the strip cannot scroll that far, the sliver is transparent, .p-cut, until the
     user scrolls or focuses it) ---------- */
  const STRIP = '.p-tabs, .p-seg';
  const placed = new WeakMap();  // strip -> the scrollLeft we set (a different value = the user scrolled it)
  function edge(s) {  // at an edge = its first / last item is wholly in view (the strip's padding may be scrolled)
    const over = s.scrollWidth > s.clientWidth + 1, b = s.getBoundingClientRect();
    const f = s.firstElementChild, l = s.lastElementChild;
    s.classList.toggle('ovf', over);
    s.classList.toggle('beg', over && (!f || f.getBoundingClientRect().left >= b.left - 1));
    s.classList.toggle('end', over && (!l || l.getBoundingClientRect().right <= b.right + 1));
    return over;
  }
  function centreOne(s, force) {
    if (!s.clientWidth) return;
    const was = placed.get(s);
    if (!force && was !== undefined && Math.abs(s.scrollLeft - was) > 1) { edge(s); return; }
    uncut(s);
    const c = pm7.$('[aria-selected=true], [aria-current], [aria-pressed=true]', s);
    if (edge(s) && c) {
      const sr = s.getBoundingClientRect(), cr = c.getBoundingClientRect();
      s.scrollLeft += (cr.left + cr.width / 2) - (sr.left + sr.width / 2);
      const b = s.getBoundingClientRect(), k = c.getBoundingClientRect();
      const cut = Array.from(s.children).find((x) => {
        const r = x.getBoundingClientRect();
        return x !== c && r.left < b.left - 1 && r.right > b.left + 1;
      });
      const r = cut && cut.getBoundingClientRect(), shown = r ? r.right - b.left : 0;
      if (r && shown < r.width / 2) {  // a sliver: scroll it out, else (strip at its end) bring all of it in, else
        const room = s.scrollWidth - s.clientWidth - s.scrollLeft;  // hide it (.p-cut) until the user scrolls; the
        if (room >= shown && k.left - shown >= b.left) s.scrollLeft += shown;  // selected item stays in view
        else if (k.right + r.width - shown <= b.right) s.scrollLeft -= r.width - shown;
        else cut.classList.add('p-cut');
      }
    }
    placed.set(s, s.scrollLeft);
    edge(s);
  }
  function centre() { pm7.$$(STRIP).forEach((s) => centreOne(s, false)); }
  function settled() {  // after the first paint, once the fonts are in (widths change when they load)
    const go = () => window.requestAnimationFrame(() => centre());
    go();
    if (d.fonts && d.fonts.ready) d.fonts.ready.then(go);
    window.addEventListener('load', go, { once: true });
  }
  function uncut(s) { pm7.$$('.p-cut', s).forEach((x) => x.classList.remove('p-cut')); }
  d.addEventListener('scroll', (e) => {
    const s = e.target;
    if (!s || !s.matches || !s.matches(STRIP)) return;
    edge(s);
    if (Math.abs(s.scrollLeft - (placed.get(s) || 0)) > 1) uncut(s);  // the user scrolled: show every item again
  }, true);
  d.addEventListener('focusin', (e) => {  // keyboard focus on a cut sliver: show it and bring it into view
    const x = e.target && e.target.closest ? e.target.closest('.p-cut') : null;
    if (!x) return;
    uncut(x.parentNode);
    x.scrollIntoView({ block: 'nearest', inline: 'nearest' });
  });
  d.addEventListener('pm7:tab', (e) => {
    const s = e.target;
    if (s && s.matches && s.matches('.p-tabs')) window.requestAnimationFrame(() => centreOne(s, true));
  });

  /* ---------- switch ---------- */
  function flip(s) {
    const on = s.getAttribute('aria-checked') !== 'true';
    s.setAttribute('aria-checked', on ? 'true' : 'false');
    const i = s.parentNode && pm7.$('input[type=hidden][name]', s.parentNode);
    if (i) {
      i.value = on ? '1' : '0';
      i.dispatchEvent(new Event('change', { bubbles: true }));
    }
  }

  /* ---------- ⋯ menu payload -> pm7.menu ---------- */
  function raw(btn) {
    try { const v = JSON.parse(btn.getAttribute('data-menu') || '[]'); return Array.isArray(v) ? v : []; }
    catch (err) { return []; }
  }
  const scopeOf = (btn) => btn.closest('[data-row-id]') || btn.closest('[data-menu-scope]') || d;
  function safe(h) { return typeof h === 'string' && h[0] === '/' && h[1] !== '/' && h[1] !== '\\'; }
  async function post(btn, it) {
    if (it.confirm || it.typed) {
      const ok = await pm7.confirm({ title: it.confirm || it.label, body: it.body, ok: it.ok || it.label,
        danger: !!it.danger, typed: it.typed, undo: it.undo, opener: btn });
      if (!ok) return;
    }
    let r;  // Accept JSON: the core's Settings answer {ok, notice} (a refusal is ok: false with status 200)
    try { r = await pm7.fetch(it.post, { form: it.vals || {}, headers: { Accept: 'application/json' } }); }
    catch (err) { r = null; }
    const a = await pm7.reply(r);
    if (!a.ok) { pm7.fail(it.label || 'Failed', r); return; }
    if (a.redirect && safe(a.redirect)) { window.location.assign(a.redirect); return; }
    if (a.notice || it.done) pm7.toast(a.notice || it.done);
    const t = it.refresh ? d.querySelector(it.refresh) : null;
    if (t && window.htmx) window.htmx.trigger(t, 'pm7:refresh');
  }
  function exec(btn, it, e) {
    const row = btn.closest('[data-row-id]');
    if (it.click) { const el = scopeOf(btn).querySelector(it.click); if (el) el.click(); return !!el; }
    if (it.det) return !!(row && pm7.table && pm7.table.det(row));
    if (it.copy != null) {
      const done = () => pm7.toast('Copied'), fail = () => pm7.toast('Copy failed', '', { error: true });
      if (navigator.clipboard) navigator.clipboard.writeText(String(it.copy)).then(done, fail); else fail();
      return true;
    }
    if (it.event) {
      btn.dispatchEvent(new CustomEvent(it.event, { bubbles: true,
        detail: { id: row ? row.getAttribute('data-row-id') : null, item: it.id || null, event: e || null } }));
      return true;
    }
    if (it.post && safe(it.post)) { post(btn, it); return true; }
    if (it.href && safe(it.href)) { window.location.assign(it.href); return true; }
    return false;
  }
  function items(btn) {
    const L = pm7.keys ? pm7.keys.label : (k) => k;
    return raw(btn).map((it) => (it.sep ? { sep: true } : {
      label: it.label, icon: it.icon, danger: !!it.danger, disabled: !!it.disabled,
      note: it.disabled && it.why ? it.why : it.note, kbd: it.key ? L(it.key) : '',
      href: it.href && safe(it.href) ? it.href : undefined,
      run: it.href ? undefined : (e) => exec(btn, it, e)
    }));
  }
  function open(btn) {
    return pm7.menu(btn, items(btn), { label: btn.getAttribute('aria-label') || 'Actions' });
  }
  function run(btn, id) {
    const it = btn ? raw(btn).find((x) => x.id === id && !x.sep) : null;
    return !!it && !it.disabled && exec(btn, it, null);
  }

  /* ---------- clicks ---------- */
  d.addEventListener('click', (e) => {
    const t = e.target;
    if (!t || !t.closest) return;
    const s = t.closest('button.sw[role=switch]');
    if (s && !s.disabled) { flip(s); return; }
    const m = t.closest('[data-menu]');
    if (m) { e.preventDefault(); open(m); return; }
    const bt = t.closest('.p-tabs[role=tablist] button[role=tab]');
    if (bt) { select(bt); return; }
    const sg = t.closest('.p-seg>a[hx-get]');
    if (sg && !e.metaKey && !e.ctrlKey && !e.shiftKey) {
      pm7.$$('a', sg.parentNode).forEach((a) => { if (a === sg) a.setAttribute('aria-current', 'true');
        else a.removeAttribute('aria-current'); });
    }
  }, true);

  pm7.menus = { open: open, items: items, run: run, exec: (btn, it) => exec(btn, it, null) };
  d.addEventListener('htmx:afterSettle', () => window.requestAnimationFrame(() => centre()));
  window.addEventListener('resize', pm7.debounce(() => centre(), 150));
  pm7.ready(settled);
})();
