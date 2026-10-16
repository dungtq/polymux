/* dt Settings body, page and popup (PRD.md §6.11 "same code"): one section at a time, the search over all
   settings, and the looks that live in this browser (pm.v7.* storage, applied by js/head.js: pm7.look) or on the
   server for all devices (POST /dt/prefs, pm.web.dt.prefs). Markup: templates/_settings_panel.html,
   settings/_*.html; the page (settings.html) or the popup (dialog#sp-dlg, js/settings-popup.js) holds it, never both
   (one #s-panel at a time).
   Global: pm7.settings = {post(url, form) -> Promise<Response | null>, refresh(selector | element), show(id),
     init(root)} (Object.assign: js/settings-popup.js adds open / close / toggle / isOpen). init(root) syncs the
     this-browser controls, the colour pickers and (popup) "Open as page" of a freshly shown body: the page on ready,
     the popup after a load.
   Sections: nav links a[data-s-go] (/settings?s=<id>) switch at once; on the page they push the URL (the server
     renders the same section on a reload, L-1), in the popup the URL stays and "Open as page" ([data-sp-page])
     follows the section. ‹ Settings (button[data-s-back], phones) goes back to the section list; popstate follows
     the URL (page only). A #hash without ?s= (/settings#security) reloads as /settings?s=<hash> (the server knows
     the aliases). On phones (< 700 px) .s-v-list shows the nav only and .s-v-sec one section (css/settings.css).
   Search: the page's filter box (js/filter.js client mode on #s-panel rows [data-p-q]) fires pm7:filter; this file
     adds .srch (every section with hits), hides groups without hits (a group whose title matches keeps its rows) and
     shows the hits per section in the nav.
   This browser: .p-seg[data-s-b] buttons, input[data-s-b], switches with data-s-b; Reset buttons [data-s-breset].
   All devices: .p-seg[data-s-a] buttons, switches and colour inputs with data-s-a, [data-s-colreset], [data-s-areset]
     (comma-joined keys back to the default, then the section reloads). Every server change toasts only after the
     server's ok (V7-CODE-5); a failure toasts the server's message and puts the control back. */
(function () {
  'use strict';
  const pm7 = window.pm7, d = document;
  const lay = () => d.getElementById('s-panel');
  const pageLay = () => d.querySelector('#main #s-panel');
  const popOf = (L) => (L ? L.closest('#sp-dlg') : null);
  function pageLink(L, id) {  // the popup's "Open as page" follows the section shown
    const a = popOf(L) && pm7.$('[data-sp-page]', popOf(L));
    if (a) a.setAttribute('href', '/settings' + (id ? '?s=' + encodeURIComponent(id) : ''));
  }
  const JSONH = { Accept: 'application/json' };

  async function post(url, form) {
    try { return await pm7.fetch(url, { form: form || {}, headers: JSONH }); } catch (e) { return null; }
  }
  function refresh(t) {
    const el = typeof t === 'string' ? d.querySelector(t) : t;
    if (el && window.htmx) window.htmx.trigger(el, 'pm7:refresh');
  }
  const secOf = (el) => el && el.closest ? el.closest('.s-sec') : null;

  /* ---------- sections ---------- */
  function show(id, how) {
    const L = lay(), sec = d.getElementById('s-sec-' + id);
    if (!L || !sec) return false;
    L.setAttribute('data-s-cur', id);
    pm7.$$('.s-sec', L).forEach((s) => { s.hidden = s !== sec; });
    pm7.$$('[data-s-go]', L).forEach((a) => {
      if (a.getAttribute('data-s-go') === id) a.setAttribute('aria-current', 'page');
      else a.removeAttribute('aria-current');
    });
    L.classList.remove('s-v-list');
    L.classList.add('s-v-sec');
    const pop = popOf(L);
    if (pop) pageLink(L, id);
    else if (how) window.history[how + 'State']({ s: id }, '', '/settings?s=' + encodeURIComponent(id));
    const m = pop ? pm7.$('[data-sp-body]', pop) : d.getElementById('main');
    if (m) m.scrollTop = 0;
    return true;
  }
  function list(how) {
    const L = lay();
    if (!L) return;
    L.classList.remove('s-v-sec');
    L.classList.add('s-v-list');
    if (popOf(L)) pageLink(L, '');
    else if (how) window.history[how + 'State']({}, '', '/settings');
  }
  function box() { return d.querySelector('input[data-p-filter="settings"]'); }
  function clearSearch() {
    const i = box();
    if (i && i.value) { i.value = ''; i.dispatchEvent(new Event('input', { bubbles: true })); }
  }

  /* ---------- search ---------- */
  function search(q) {
    const L = lay();
    if (!L) return;
    const on = !!q;
    L.classList.toggle('srch', on);
    let all = 0;
    pm7.$$('.s-sec', L).forEach((s) => {
      let n = 0;
      pm7.$$('.s-grp', s).forEach((g) => {
        const rows = pm7.$$('[data-p-q]', g);
        const self = on && pm7.filter.match(g.getAttribute('data-s-q') || '', q);
        if (self) rows.forEach((r) => { r.hidden = false; });
        const hit = rows.filter((r) => !r.hidden).length;
        const keep = !on || hit > 0 || self;
        g.toggleAttribute('data-s-off', !keep);
        if (on && keep) n += hit || 1;
      });
      s.toggleAttribute('data-s-off', on && !n);
      s.hidden = on ? !n : s.getAttribute('data-s-sec') !== L.getAttribute('data-s-cur');
      const c = L.querySelector('[data-s-go="' + s.getAttribute('data-s-sec') + '"] [data-s-hits]');
      if (c) { c.textContent = n ? String(n) : ''; c.hidden = !on || !n; }
      all += n;
    });
    const none = L.querySelector('[data-s-none]');
    if (none) none.hidden = !on || all > 0;
    pm7.$$('.p-tabpanel', L).forEach((t) => {  // [hidden] is display:none!important (base.css): set it, never override
      const tab = d.getElementById(t.getAttribute('aria-labelledby') || '');
      t.hidden = !on && !!tab && tab.getAttribute('aria-selected') !== 'true';
    });
  }
  d.addEventListener('pm7:filter', (e) => { if (e.detail && e.detail.name === 'settings') search(e.detail.q); });

  /* ---------- this browser ---------- */
  const BDEF = { 'shell.mode': 'auto', 'shell.rail': 'open', 'shell.railmode': 'all', 'font.size': '100',
    'keys.toast': 'key', 'aside.pct': '30', 'keys.hints': '1', 'side.sess': '1' };
  const FONTS = ['font.ui', 'font.chat', 'font.mono', 'font.size', 'chat.size', 'cmp.size'];
  function bget(k) { return pm7.store.get(k, BDEF[k] || ''); }
  function bset(k, v) {
    v = v == null ? '' : String(v).trim();
    const lk = pm7.look, now = lk && k === 'shell.railmode' ? lk.rail() : '';
    if (v === '' || v === BDEF[k] || ((k === 'chat.size' || k === 'cmp.size') && !(+v >= 10))) pm7.store.remove(k);
    else pm7.store.set(k, v);
    /* the sidebar keeps its look: a new mode starts from this tab's state; Sidebar sets this tab's state too */
    if (lk && now) lk.rail(now);
    if (lk && k === 'shell.rail' && bget('shell.railmode') === 'tab') lk.rail(v || BDEF[k]);
    if (lk) lk.apply();
  }
  function syncB(root) {
    pm7.$$('.p-seg[data-s-b]', root).forEach((g) => {
      const v = bget(g.getAttribute('data-s-b'));
      pm7.$$('button', g).forEach((b) => b.setAttribute('aria-pressed', b.value === v ? 'true' : 'false'));
    });
    pm7.$$('input[data-s-b]', root).forEach((i) => {
      if (i !== d.activeElement) i.value = bget(i.getAttribute('data-s-b'));
    });
    pm7.$$('button.sw[data-s-b]', root).forEach((s) => setSwitch(s, bget(s.getAttribute('data-s-b')) !== '0'));
  }
  function setSwitch(s, on) {
    s.setAttribute('aria-checked', on ? 'true' : 'false');
    const i = s.parentNode && pm7.$('input[type=hidden]', s.parentNode);
    if (i) i.value = on ? '1' : '0';
  }
  function labelOf(el) {
    const r = el.closest('.s-row');
    const b = r && pm7.$('.s-lab b', r);
    return b ? b.textContent.trim() : (el.getAttribute('aria-label') || 'Setting');
  }

  /* ---------- all devices (/dt/prefs) ---------- */
  async function save(change, what) {
    const r = await post('/dt/prefs', change);
    const a = await pm7.reply(r);
    if (!a.ok) { pm7.fail('Not saved', r); return false; }
    if (pm7.look) pm7.look.prefs(a.json);
    pm7.toast('Saved · ' + what);
    return true;
  }
  function hex(c) {
    const m = /rgba?\((\d+),\s*(\d+),\s*(\d+)/.exec(c || '');
    return m ? '#' + [m[1], m[2], m[3]].map((x) => (+x).toString(16).padStart(2, '0')).join('') : '';
  }
  let probe = null;
  function tokColour(key) {
    const tok = pm7.look ? pm7.look.tok(key) : '';
    if (!tok) return '';
    if (!probe) { probe = pm7.el('span.vh', { 'aria-hidden': 'true' }); d.body.appendChild(probe); }
    probe.style.setProperty('color', 'var(' + tok + ')');
    return hex(window.getComputedStyle(probe).color);
  }
  function syncColours(root) {  // a picker without a stored value shows the theme's colour of its token
    pm7.$$('input[type=color][data-s-a]', root).forEach((i) => {
      if (!i.hasAttribute('data-s-set')) { const v = tokColour(i.getAttribute('data-s-a')); if (v) i.value = v; }
    });
  }
  function colourState(i, v) {
    const b = i.closest('.s-col');
    if (v) i.setAttribute('data-s-set', ''); else i.removeAttribute('data-s-set');
    if (!b) return;
    const t = pm7.$('.s-cv', b), x = pm7.$('[data-s-colreset]', b);
    if (t) t.textContent = v || 'default';
    if (x) x.hidden = !v;
  }

  /* ---------- clicks ---------- */
  d.addEventListener('click', async (e) => {
    const t = e.target && e.target.closest ? e.target : null;
    if (!t || !lay()) return;
    const go = t.closest('a[data-s-go]');
    if (go && !e.metaKey && !e.ctrlKey && !e.shiftKey && !e.altKey) {
      e.preventDefault();
      clearSearch();
      show(go.getAttribute('data-s-go'), 'push');
      return;
    }
    if (t.closest('[data-s-back]')) { list('push'); return; }
    if (t.closest('[data-s-clear]')) { const i = box(); if (i) pm7.filter.clear(i); return; }
    const bb = t.closest('.p-seg[data-s-b]>button');
    if (bb) {
      const k = bb.parentNode.getAttribute('data-s-b');
      bset(k, bb.value);
      syncB(lay());
      if (k === 'shell.mode') syncColours(lay());
      pm7.toast('Saved in this browser · ' + labelOf(bb) + ': ' + bb.textContent.trim());
      return;
    }
    const br = t.closest('[data-s-breset]');
    if (br) {
      (br.getAttribute('data-s-breset') === 'fonts' ? FONTS : ['aside.pct']).forEach((k) => pm7.store.remove(k));
      if (pm7.look) pm7.look.apply();
      syncB(lay());
      pm7.toast(br.getAttribute('data-s-breset') === 'fonts' ? 'Fonts reset' : 'Panel widths reset');
      return;
    }
    const ab = t.closest('.p-seg[data-s-a]>button');
    if (ab && ab.getAttribute('aria-pressed') !== 'true') {
      const g = ab.parentNode, k = g.getAttribute('data-s-a');
      if (await save({ [k]: ab.value }, labelOf(ab) + ': ' + ab.textContent.trim())) {
        pm7.$$('button', g).forEach((b) => b.setAttribute('aria-pressed', b === ab ? 'true' : 'false'));
        pm7.$$('[data-s-when^="' + k + '="]', lay()).forEach((w) => {
          w.hidden = w.getAttribute('data-s-when') !== k + '=' + ab.value;
        });
      }
      return;
    }
    const cr = t.closest('[data-s-colreset]');
    if (cr) {
      const k = cr.getAttribute('data-s-colreset'), i = pm7.$('input[type=color]', cr.closest('.s-col'));
      if (await save({ [k]: '' }, labelOf(cr) + ': default') && i) {
        colourState(i, '');
        syncColours(cr.closest('.s-col'));
      }
      return;
    }
    const ar = t.closest('[data-s-areset]');
    if (ar) {
      const ch = {};
      ar.getAttribute('data-s-areset').split(',').filter(Boolean).forEach((k) => { ch[k] = ''; });
      if (await save(ch, 'back to the defaults')) refresh(secOf(ar));
    }
  });

  /* ---------- changes ---------- */
  d.addEventListener('change', async (e) => {
    const t = e.target;
    if (!t || !t.closest || !lay() || !lay().contains(t)) return;
    if (t.matches('input[data-s-b]')) {
      bset(t.getAttribute('data-s-b'), t.value);
      pm7.toast('Saved in this browser · ' + labelOf(t));
      return;
    }
    const sw = t.matches('input[type=hidden]') && t.parentNode ? pm7.$('button.sw', t.parentNode) : null;
    if (sw && sw.hasAttribute('data-s-b')) {
      bset(sw.getAttribute('data-s-b'), t.value === '1' ? '1' : '0');
      pm7.toast('Saved in this browser · ' + labelOf(sw) + (t.value === '1' ? ' on' : ' off'));
      return;
    }
    if (sw && sw.hasAttribute('data-s-a')) {
      const on = t.value === '1';
      if (!await save({ [sw.getAttribute('data-s-a')]: on ? '1' : '0' }, labelOf(sw) + (on ? ' on' : ' off'))) {
        setSwitch(sw, !on);
      }
      return;
    }
    if (t.matches('input[type=color][data-s-a]')) {
      const k = t.getAttribute('data-s-a'), v = t.value.toLowerCase();
      if (await save({ [k]: v }, labelOf(t) + ': ' + v)) colourState(t, v);
      else if (pm7.look) pm7.look.load();
      return;
    }
    const ui = t.closest('form[data-s-ui]');
    if (ui && t.name === 'ui') ui.requestSubmit();  // a plain GET ?ui= (the server sets the cookie, never JS)
  });
  /* a colour shows while it is picked; it is saved on change */
  d.addEventListener('input', (e) => {
    const t = e.target;
    if (!t || !t.matches || !t.matches('input[type=color][data-s-a]') || !pm7.look) return;
    const tok = pm7.look.tok(t.getAttribute('data-s-a'));
    if (tok && tok !== '--bub-asst-bg') d.documentElement.style.setProperty(tok, t.value);
  });

  window.addEventListener('popstate', () => {
    if (!pageLay()) return;
    const s = new URLSearchParams(window.location.search).get('s');
    if (!(s && show(s, null))) list(null);
  });
  d.addEventListener('htmx:afterSettle', (e) => {
    const L = lay();
    if (!L || !e.target || !L.contains(e.target)) return;
    syncB(e.target);
    syncColours(e.target);
  });

  function fromHash() {  // /settings#security: the server resolves the alias and renders that section
    const h = window.location.hash.slice(1);
    if (!pageLay() || !h || !/^[\w-]{1,32}$/.test(h)) return false;
    if (new URLSearchParams(window.location.search).get('s')) return false;
    window.location.replace('/settings?s=' + encodeURIComponent(h));
    return true;
  }
  window.addEventListener('hashchange', fromHash);

  function init(root) {
    const L = root && root.id === 's-panel' ? root : pm7.$('#s-panel', root || d);
    if (!L) return;
    syncB(L);
    syncColours(L);
    pageLink(L, L.classList.contains('s-v-sec') ? L.getAttribute('data-s-cur') : '');
  }
  pm7.settings = Object.assign(pm7.settings || {}, { post: post, refresh: refresh, init: init,
    show: (id) => show(id, 'push') });
  pm7.ready(() => {
    const L = pageLay();
    if (!L || fromHash()) return;
    init(L);
  });
})();
