/* dt theme (pm v7) search dialog (#x-pal): one combobox over three scopes.
     all (⌘⇧P, ⌘K, the search button): open tabs, sessions, pages, commands
     sessions (⌘P, the + in the tab strip): open tabs, then the other sessions (running / waiting / failed first)
     commands: the input starts with `>`; the rows are pm7.keys.commands() (the key table)
   ↑ ↓ move (aria-activedescendant on the input), ↵ or click opens, as a popup where there is one: an agent (a
   session row in scope all, an agent / ended / chat hit /a/<id>) opens in the chat aside (pm7.aside.open; ended
   hits with {state: 'ended'}), a project hit /p/<name> in the project quick look (pm7.peek.open, when loaded), a
   page whose sidebar link has data-sp-open (Settings) in the Settings popup (pm7.settings.open); in scope sessions a
   session opens in a tab (pm7.tabs.open); a tab or other page navigates, a command runs after the dialog closed.
   ⌘ / Ctrl ↵ (or a ⌘ / Ctrl / Shift click) = the row's "as a page" action instead (alt: an agent in a tab, a
   project or Settings as its page); the footer's [data-pfhint] names it while such a row is selected.
   Esc and the backdrop close it (layers.js).
   Globals: pm7.search = {open(scope, q), toggle(scope), close()}.
   Hooks: dialog#x-pal with an input (role=combobox), a [role=listbox] list, optional [data-scope] / .scope label,
   [data-pfhint] and [data-pfopen] (the footer's ↵ word: what the selected row does); missing parts are created here.
   Data is read from the page: .x-nav / .x-sfoot a[data-nav],
   #x-sess a.x-sr[data-sess-id] (group details.x-sg; data-title = window name), #x-tabs .x-tab[data-tab-kind] >
   a[role=tab]. Matches are wrapped in <mark> built with DOM nodes (no HTML strings).
   Server hits (scope all, not commands, from 3 characters): the core's GET /search?q= through pm7.fetch, debounced;
   its dt fragment (templates/_search.html: ul[data-sg][data-title] > li[data-href] with .l .d .r and a sprite icon)
   is parsed with DOMParser (nothing runs) into rows after the sessions: projects, tmux, ended agents, chats, Quick
   chat, files. A hit whose link a local row shown for this query already has (a live agent in the sidebar, a page)
   is dropped; a row the term filtered out never hides a hit. A project or page named exactly as typed (any case)
   moves to the top, its group with it, and is selected (unless ↑ ↓ already picked a row); a command named as a shown
   page (Settings) is dropped, the page row opens the popup. A Quick chat hit (/?qchat=<id>) opens the conversation
   in place (pm7.qchat.open); agent and project hits open as above; every other hit navigates. A late answer
   for an older query is ignored. */
(function () {
  'use strict';
  const pm7 = window.pm7, d = document;
  let scope = null, idx = 0, items = [], moved = false;  // moved: the user picked a row with ↑ ↓ for this query
  const MIN_Q = 3;  // the core searches content (chats, files, Quick chat) from 3 characters
  let srv = { q: '', out: [] }, seq = 0;

  const pal = () => d.getElementById('x-pal');
  const el = pm7.el;  // core.js: el(tag, cls, text)
  function txt(n) { return ((n && n.textContent) || '').replace(/\s+/g, ' ').trim(); }

  /* the parts of #x-pal; whatever the template did not provide is built (mock structure) */
  function parts() {
    const P = pal();
    if (!P) return null;
    let input = pm7.$('input', P), list = pm7.$('[role=listbox]', P);
    if (!input) {
      const row = el('div', 'x-pin'), ic = pm7.icon('search');
      input = el('input');
      input.type = 'text';
      if (ic) row.appendChild(ic);
      row.append(el('span', 'scope'), input);
      P.prepend(row);
    }
    if (!list) {
      const row = input.closest('.x-pin');
      list = el('ul', 'x-pl');
      P.insertBefore(list, row && row.parentNode === P ? row.nextSibling : null);
    }
    if (!list.id) list.id = 'x-pal-list';
    list.setAttribute('role', 'listbox');
    input.setAttribute('role', 'combobox');
    input.setAttribute('aria-controls', list.id);
    input.setAttribute('aria-expanded', 'true');
    input.setAttribute('aria-autocomplete', 'list');
    input.autocomplete = 'off';
    input.spellcheck = false;
    if (!input.getAttribute('aria-label')) input.setAttribute('aria-label', 'Search');
    return { P: P, input: input, list: list, scope: pm7.$('[data-scope], .scope', P), hint: pm7.$('[data-pfhint]', P),
      verb: pm7.$('[data-pfopen]', P) };
  }

  /* ---------- data from the page ---------- */
  function tabs() {
    return pm7.$$('#x-tabs .x-tab[data-tab-kind]').map((t) => {
      const a = t.querySelector('a[role=tab]') || t;
      return { g: 'Open tabs', label: txt(t.querySelector('.nm')) || a.getAttribute('title') || '',
        desc: t.getAttribute('data-tab-kind') === 'term' ? 'read-only terminal' : t.getAttribute('data-tab-id'),
        lead: t.querySelector('.dot, svg'), id: t.getAttribute('data-tab-id'),
        right: a.getAttribute('aria-selected') === 'true' || t.classList.contains('on') ? 'current' : 'tab',
        href: a.getAttribute('href'), verb: 'switch to the tab', run: () => go(a.getAttribute('href')) };
    });
  }
  function sessions() {
    return pm7.$$('#x-sess a.x-sr[data-sess-id]').map((r) => {
      const g = r.closest('details.x-sg'), s = g && g.querySelector('summary'), id = r.getAttribute('data-sess-id');
      const label = r.getAttribute('data-label') || txt(r.querySelector('.nm')) || id;
      const proj = (g && g.getAttribute('data-project')) || txt(s), name = r.getAttribute('data-title') || '';
      const dot = r.querySelector('.dot'), age = r.querySelector('[data-age], .a');
      const desc = [label !== id ? id : '', name && name !== label ? name : '', proj].filter(Boolean).join(' · ');
      const href = r.getAttribute('href'), tab = { verb: 'open in a tab', run: () => pm7.tabs.open('agent', id, href) };
      return Object.assign({ g: 'Sessions', label: label, desc: desc, lead: dot, id: id,
        busy: !!dot && ['running', 'waiting', 'failed'].some((c) => dot.classList.contains(c)),
        right: txt(age), href: href }, scope === 'sessions' ? tab : agent(id, null, tab));
    });
  }
  function pages() {
    return pm7.$$('.x-nav a[data-nav], .x-sfoot a[data-nav]').map((a) => {
      const href = a.getAttribute('href'), page = { verb: 'go to the page', run: () => go(href) };
      const sp = a.getAttribute('data-sp-open'), pop = sp != null && pm7.settings && pm7.settings.open;
      return Object.assign({ g: 'Pages', label: txt(a.querySelector('.t')) || txt(a), desc: '',
        lead: a.querySelector('svg'), right: txt(a.querySelector('kbd')), href: href },
      pop ? { verb: 'open in a popup', run: () => pm7.settings.open(sp), alt: page } : page);
    });
  }
  function commands() {
    return (pm7.keys ? pm7.keys.commands() : []).map((c) => ({
      g: 'Commands', label: c.label, desc: '', lead: null, kbd: c.keys[0] || '', verb: 'run', run: c.run }));
  }
  function go(href) { if (href && href[0] === '/' && href[1] !== '/') window.location.assign(href); }
  /* an agent: ↵ = the chat aside (ended: state 'ended'), alt = a tab; no aside (an embed page): the tab */
  function agent(id, state, tab) {
    if (!pm7.aside || !pm7.aside.open) return tab;
    return { verb: 'open in the aside', alt: tab, run: () => {
      if (!pm7.aside.open(id, state ? { state: state } : undefined)) tab.run();
    } };
  }
  function dec(s) { try { return decodeURIComponent(s); } catch (e) { return ''; } }

  /* ---------- server hits: GET /search (the core route, dt fragment _search.html) ---------- */
  function norm(h) {
    try { const u = new URL(h || '', window.location.origin); return decodeURIComponent(u.pathname) + u.search; }
    catch (e) { return h || ''; }
  }
  function hit(href, key) {  // what ↵ does with a server hit of group key: {verb, run, alt?}
    const m = /^\/\?qchat=(\d+)$/.exec(href || '');
    if (m && pm7.qchat) return { verb: 'open in Quick chat', run: () => pm7.qchat.open(Number(m[1])) };
    const page = { verb: 'go to the page', run: () => go(href) };
    const a = /^\/a\/([^/?#]+)$/.exec(href || ''), p = /^\/p\/([^/?#]+)$/.exec(href || '');
    const id = a ? dec(a[1]) : '', name = p ? dec(p[1]) : '';
    if (id) {
      const tab = pm7.tabs ? { verb: 'open in a tab', run: () => pm7.tabs.open('agent', id, href) } : page;
      return agent(id, key === 'ended' ? 'ended' : null, tab);
    }
    if (name && pm7.peek && pm7.peek.open) return { verb: 'quick look', run: () => pm7.peek.open(name), alt: page };
    return page;
  }
  function parse(html) {
    const doc = new DOMParser().parseFromString(html, 'text/html'), out = [];
    pm7.$$('ul[data-sg]', doc).forEach((ul) => {
      const g = ul.getAttribute('data-title') || ul.getAttribute('data-sg'), key = ul.getAttribute('data-sg');
      pm7.$$('li[data-href]', ul).forEach((li) => {
        const href = li.getAttribute('data-href');
        out.push(Object.assign({ g: g, label: txt(li.querySelector('.l')), desc: txt(li.querySelector('.d')),
          href: href, lead: li.querySelector('svg'), right: txt(li.querySelector('.r')), srv: true, sg: key },
        hit(href, key)));
      });
    });
    return out;
  }
  function wantSrv(raw) {
    const q = raw.trim().toLowerCase();
    return scope === 'all' && raw.charAt(0) !== '>' && q.length >= MIN_Q ? q : '';
  }
  const ask = pm7.debounce(async (q) => {
    const n = ++seq;
    let out = [];
    try {
      const r = await pm7.fetch('/search?q=' + encodeURIComponent(q));
      if (r.ok && r.status !== 204) out = parse(await r.text());
    } catch (e) { out = []; }  // offline or aborted: the local rows stay; a 5xx is js/reload.js's
    if (n !== seq || !isOpen()) return;
    srv = { q: q, out: out };
    render(true);
  }, 250);
  function server(raw) {
    const q = wantSrv(raw);
    if (!q) { seq++; srv = { q: '', out: [] }; return; }
    if (q !== srv.q) ask(q);
  }

  function build(raw) {
    const cmd = raw.charAt(0) === '>', q = (cmd ? raw.slice(1) : raw).trim().toLowerCase();
    const m = (o) => !q || (o.label + ' ' + o.desc + ' ' + (o.id || '')).toLowerCase().indexOf(q) >= 0;
    if (cmd) return { q: q, name: 'Commands', out: commands().filter(m) };
    const open = tabs(), ids = new Set(open.map((t) => t.id));
    const rest = sessions().filter((s) => !ids.has(s.id));
    if (scope === 'sessions') {
      const busy = rest.filter((s) => s.busy).map((s) => Object.assign(s, { g: 'Running and waiting' }));
      const idle = rest.filter((s) => !s.busy).map((s) => Object.assign(s, { g: 'Recent' }));
      return { q: q, name: 'Sessions', out: open.concat(busy, idle).filter(m) };
    }
    const local = open.filter(m).concat(rest.filter(m).slice(0, q ? 8 : 6));
    const pg = pages().filter(m), seen = new Set(local.concat(pg).map((o) => norm(o.href)));  // rows shown only
    const hits = srv.q && srv.q === wantSrv(raw) ? srv.out.filter((o) => !seen.has(norm(o.href))) : [];
    const named = new Set(pg.map((o) => o.label.toLowerCase()));  // a page wins over its command (Settings)
    const cmds = commands().filter((c) => m(c) && !named.has(c.label.toLowerCase())).slice(0, q ? 8 : 4);
    const out = exact(local.concat(hits, pg, cmds), q);
    return { q: q, name: 'Everything', out: out, wait: !!wantSrv(raw) && srv.q !== wantSrv(raw) };
  }
  /* a project or page named exactly q (any case) leads: its group moves to the top with that row first */
  function exact(out, q) {
    const top = q && out.find((o) => (o.g === 'Pages' || o.sg === 'project') && o.label.toLowerCase() === q);
    if (!top) return out;
    return [top].concat(out.filter((o) => o.g === top.g && o !== top), out.filter((o) => o.g !== top.g));
  }

  /* ---------- render ---------- */
  function marked(text, q) {  // text with the first match wrapped in <mark>, as nodes
    const i = q ? text.toLowerCase().indexOf(q) : -1;
    if (i < 0) return [text];
    const mk = el('mark', '', text.slice(i, i + q.length));
    return [text.slice(0, i), mk, text.slice(i + q.length)];
  }
  function key(o) { return o ? o.g + '\n' + o.label + '\n' + (o.href || '') : ''; }
  function render(keep) {
    const p = parts();
    if (!p) return;
    const was = keep && moved ? key(items[idx]) : '', b = build(p.input.value);  // else the top row (exact)
    items = b.out;
    const at = was ? items.findIndex((o) => key(o) === was) : -1;
    idx = at >= 0 ? at : Math.max(0, Math.min(idx, items.length - 1));
    const rows = [];
    let g = null;
    items.forEach((o, i) => {
      if (o.g !== g) {
        g = o.g;
        const h = el('li', 'x-pg', g);
        h.setAttribute('role', 'presentation');
        rows.push(h);
      }
      const li = el('li', 'x-pi' + (i === idx ? ' on' : ''));
      li.id = 'x-pi-' + i;
      li.setAttribute('role', 'option');
      li.setAttribute('aria-selected', i === idx ? 'true' : 'false');
      li.setAttribute('data-pi', String(i));
      const lead = o.lead ? o.lead.cloneNode(true) : pm7.icon(o.g === 'Commands' ? 'chev' : 'search');
      if (lead) li.appendChild(lead);
      const l = el('span', 'l');
      l.append(...marked(o.label, b.q));
      li.append(l, el('span', 'd', o.desc));
      const r = el('span', 'r');
      if (o.kbd) r.appendChild(el('kbd', '', o.kbd)); else if (o.right) r.textContent = o.right;
      li.appendChild(r);
      rows.push(li);
    });
    if (!items.length) {
      const e = el('li', 'x-pempty');
      if (b.wait) e.append('Searching…');
      else e.append('Nothing matches. Type ', el('kbd', '', '>'), ' for commands.');
      rows.push(e);
    }
    p.list.replaceChildren(...rows);
    if (items.length) p.input.setAttribute('aria-activedescendant', 'x-pi-' + idx);
    else p.input.removeAttribute('aria-activedescendant');
    if (p.scope) p.scope.textContent = b.name;
    say(p);
  }
  function say(p) {  // the footer's ↵ word and hint follow the selected row
    const o = items[idx], lab = (s) => (pm7.keys ? pm7.keys.label(s) : '');
    if (p.verb) p.verb.textContent = (o && o.verb) || 'open';
    if (!p.hint) return;
    if (o && o.alt) p.hint.replaceChildren(el('kbd', '', lab('mod+Enter')), ' ' + o.alt.verb);
    else p.hint.replaceChildren(el('kbd', '', scope === 'sessions' ? lab('mod+shift+P') : '>'),
      scope === 'sessions' ? ' search everything' : ' commands only');
  }
  function move(i) {
    const p = parts();
    if (!p || !items.length) return;
    idx = (i + items.length) % items.length;
    moved = true;
    pm7.$$('.x-pi', p.list).forEach((li) => {
      const on = Number(li.getAttribute('data-pi')) === idx;
      li.classList.toggle('on', on);
      li.setAttribute('aria-selected', on ? 'true' : 'false');
      if (on) li.scrollIntoView({ block: 'nearest' });
    });
    p.input.setAttribute('aria-activedescendant', 'x-pi-' + idx);
    say(p);
  }
  function run(i, page) {  // page: ⌘ / Ctrl ↵ or a modified click, the row's alt ("as a page") when it has one
    const o = items[i];
    if (!o) return;
    close();
    setTimeout(page && o.alt ? o.alt.run : o.run, 0);  // after the dialog left the stack (a command may open a layer)
  }

  /* ---------- open / close ---------- */
  function isOpen() { const P = pal(); return !!P && P.open; }
  function open(s, q) {
    const p = parts();
    if (!p) return;
    scope = s === 'sessions' ? 'sessions' : 'all';
    p.input.value = q != null ? q : s === 'cmd' ? '>' : '';
    p.input.placeholder = scope === 'sessions' ? 'Go to session: name, project or id'
      : 'Search sessions, projects, chats and files.  > for commands';
    idx = 0;
    moved = false;
    srv = { q: '', out: [] };
    server(p.input.value);
    render();
    if (!isOpen()) pm7.dialog(p.P, { focus: p.input, onClose: () => { scope = null; seq++; } });
    else p.input.focus();
  }
  function toggle(s) {
    const want = s === 'sessions' ? 'sessions' : 'all';
    if (isOpen() && scope === want && s !== 'cmd') close(); else open(s);
  }
  function close() { const P = pal(); if (P && isOpen()) pm7.layers.close(P); }

  d.addEventListener('input', (e) => {
    if (!e.target.closest || !e.target.closest('#x-pal')) return;
    idx = 0;
    moved = false;
    server(e.target.value);
    render();
  });
  d.addEventListener('keydown', (e) => {
    if (e.isComposing || !e.target.closest || !e.target.closest('#x-pal') || !pm7.isField(e.target)) return;
    if (e.key === 'ArrowDown') { e.preventDefault(); move(idx + 1); }
    else if (e.key === 'ArrowUp') { e.preventDefault(); move(idx - 1); }
    else if (e.key === 'Enter') { e.preventDefault(); run(idx, e.metaKey || e.ctrlKey); }
  });
  d.addEventListener('click', (e) => {
    const li = e.target.closest ? e.target.closest('#x-pal [data-pi]') : null;
    if (li) { e.preventDefault(); run(Number(li.getAttribute('data-pi')), e.metaKey || e.ctrlKey || e.shiftKey); }
  });

  pm7.search = { open: open, toggle: toggle, close: close };
})();
