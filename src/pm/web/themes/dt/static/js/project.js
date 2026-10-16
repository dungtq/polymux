/* dt project view (PRD.md §6.4, LAYOUT.md §5). Markup: templates/project.html, project/_*.html; parts JS:
   parts.js (button tabs: aria-selected + panels, the ⋯ / Git ▾ menu), table.js, listkeys.js, filter.js (sidebar
   filter), inbox.js (the agent rows' hooks in #pj-agents). This file adds only what is the project view's own:
   - tabs (PARTS.md §14): one .pj-tab section shows; when one becomes visible (a click, a tile, the hash, the
     first paint) the event pm7:pj-tab {key} fires on document, so tab partials load lazily. The URL hash keeps the
     tab: #tab=<key> (Files adds &path=<path>); a tile ([data-pj-goto]) opens its tab.
   - project sidebar: >= 900px collapse / expand (⌘⇧B, [data-pj-navc] / [data-pj-navo]; remembered in the cookie
     pm_v7_pjnav, which the server reads to render the final layout, LAYOUT L-1); < 900px it is off-canvas
     (.pj-lay.navo, a layers.js soft layer with the scrim: Esc / scrim close it). [data-pj-tc] folds a project's
     agents; a click on an agent row (a[data-pj-ag]) opens its chat in the aside (a modified click follows the link).
   - head: [data-pj-launch] opens the Launch dialog (pm7.launch.open({project})) when it is there; Git ▾ items fire
     pj:git {item}: fetch / pull / push first show what they will do (GET <base>/git/plan?action=, fetch: fixed
     text), then post (a pull whose plan says "clash" uses /git/pull-stash); Restart stack (plain confirm) and Stop
     agents (typed PANIC, POST /panic scope=project:<name>). Toasts only after the server said OK (V7-CODE-5).
     Git ▾ Remotes… opens the remotes panel (pm7.pjs, js/pjsettings.js). tmux tab: Close all ([data-pj-closeall],
     typed project name, POST <base>/tmux/close-all, then #pj-agents refreshes and carries #pj-tmux out of band).
   - Files ([data-pj-files]): the core fragment loads on first show (once, also from #browse=); a[data-pj-in] links
     load in place; the README follows the listing at the root; a path that is not there shows a status line with a
     link back to the project root. Docs: [data-pj-doc] buttons load /view/<key>; a lazy first document
     ([data-pj-lazy]) loads on first show: the choices in order (not [data-pj-miss] / [data-pj-list]) until one is a
     document. Docs › Skills: [data-pjk-scope] Local | Global; a row (event pj:skill) opens its SKILL.md in the reader.
     Plans: a row (event pj:plan from listkeys ↵ / click) opens its plan in the reader.
   - reader aside (#pj-reader): pm7.pj.reader.open({title, src | url, href, cl}) shows a page in a frame (src) or
     a fragment (url, loaded with htmx; in .x-cl unless cl: false, a dt fragment); href = the "Open as a page" link
     (default src | url, '' = none); a grid column >= 1560px, an overlay below (Esc: a soft layer).
   - classic hash links (#browse=, #view=, #skill=, #issues, #backlog, #issue=N) open the dt tab on load and on
     hashchange; #issue=N then calls pm7.backlog.reader(project, N) (one setTimeout 0: after every ready hook).
   Globals: pm7.pj = {tab(key), refresh() (the head's stats line and the tiles from a fresh render; tab partials call
   it after an action), reader: {open, close, isOpen}, nav: {toggle, open, close}, hash: {get, set} (the URL hash as
   URLSearchParams; set = replaceState)}.
   Keys: ⌘⇧B through pm7.keys.register (scope "Project", only on this page). No interval, no keydown listener. */
(function () {
  'use strict';
  const pm7 = window.pm7, d = document;
  const COOKIE = 'pm_v7_pjnav', NAVK = 'mod+shift+B';
  const lay = () => d.getElementById('pj-lay');
  const base = () => { const l = lay(); return l ? l.getAttribute('data-pj-base') : ''; };
  const name = () => { const l = lay(); return l ? l.getAttribute('data-pj') : ''; };
  const plain = (e) => !(e.button || e.metaKey || e.ctrlKey || e.shiftKey || e.altKey);
  const label = (k) => (pm7.keys ? pm7.keys.label(k) : k);
  function load(url, target) {
    if (!window.htmx || !target) return Promise.resolve();
    return window.htmx.ajax('GET', url, { target: target, swap: 'innerHTML', source: target });
  }

  /* ---------- tabs and the URL hash ---------- */
  function hashState() {
    try { return new URLSearchParams(window.location.hash.replace(/^#/, '')); }
    catch (e) { return new URLSearchParams(); }
  }
  function setHash(p) {
    const s = p.toString(), url = window.location.pathname + window.location.search + (s ? '#' + s : '');
    try { window.history.replaceState(window.history.state, '', url); } catch (e) { /* ignore */ }
  }
  const tabBtn = (key) => d.querySelector('#pj-main .p-tabs [data-p-tab="' + CSS.escape(key) + '"]');
  function shown(key, keepHash) {
    if (!keepHash) {
      const p = hashState();
      if (p.get('tab') !== key) { p.forEach((v, k) => { if (k !== 'tab') p.delete(k); }); }
      p.set('tab', key);
      setHash(p);
    }
    d.dispatchEvent(new CustomEvent('pm7:pj-tab', { detail: { key: key } }));
  }
  let keepNext = false;  // a tab chosen from the hash: keep the hash as it is (Files' path)
  function tab(key, keepHash) {
    const b = tabBtn(key);
    if (!b) return false;
    if (b.getAttribute('aria-selected') === 'true') { shown(key, !!keepHash); return true; }
    keepNext = !!keepHash;
    b.click();  // parts.js selects it and fires pm7:tab (-> shown), synchronously
    keepNext = false;
    return true;
  }
  d.addEventListener('pm7:tab', (e) => {
    if (!e.target.closest || !e.target.closest('#pj-main') || !e.detail) return;
    shown(e.detail.key, keepNext);
  });
  window.addEventListener('hashchange', () => {
    if (classic()) return;
    const k = hashState().get('tab');
    if (k) tab(k, true);
  });

  /* ---------- project sidebar ---------- */
  function wide() { return !pm7.overlay(); }
  function navOpen() { const l = lay(); return !!l && l.classList.contains('navo'); }
  let opener = null;
  function expanded() {
    const l = lay();
    if (!l) return;
    const on = wide() ? !l.classList.contains('navc') : navOpen();
    pm7.$$('[data-pj-navc], [data-pj-navo]', l).forEach((b) => b.setAttribute('aria-expanded', on ? 'true' : 'false'));
  }
  function openNav() {
    const l = lay();
    if (!l || navOpen()) return;
    opener = d.activeElement;
    l.classList.add('navo');
    pm7.layers.sync();
    expanded();
    const cur = pm7.$('.pj-np.on .pj-nn', l) || pm7.$('.pj-nn', l);
    if (cur) cur.focus({ preventScroll: true });
  }
  function closeNav() {
    const l = lay();
    if (!l || !navOpen()) return;
    const inside = pm7.$('#pj-nav', l).contains(d.activeElement);
    l.classList.remove('navo');
    pm7.layers.sync();
    expanded();
    if (inside && opener && d.contains(opener)) opener.focus({ preventScroll: true });
    opener = null;
  }
  function remember(collapsed) {
    const secure = window.location.protocol === 'https:' ? '; secure' : '';
    d.cookie = COOKIE + '=' + (collapsed ? '1' : '0') + '; path=/; max-age=31536000; samesite=lax' + secure;
  }
  function toggleNav(withToast) {
    const l = lay();
    if (!l) return false;
    if (!wide()) {
      if (navOpen()) closeNav(); else openNav();
      if (withToast) pm7.toast(navOpen() ? 'Project sidebar open' : 'Project sidebar closed', label(NAVK));
      return true;
    }
    const c = l.classList.toggle('navc');
    remember(c);
    expanded();
    if (withToast) pm7.toast(c ? 'Project sidebar collapsed' : 'Project sidebar expanded', label(NAVK));
    const f = c ? pm7.$('[data-pj-navo]', l) : pm7.$('[data-pj-navc]', l);
    if (f && l.contains(d.activeElement)) f.focus({ preventScroll: true });
    return true;
  }
  function fold(b) {
    const g = d.getElementById(b.getAttribute('aria-controls') || '');
    if (!g) return;
    const on = b.getAttribute('aria-expanded') !== 'true';
    b.setAttribute('aria-expanded', on ? 'true' : 'false');
    const p = b.closest('.pj-np'), nm = p && pm7.$('.pj-nn .nm', p);
    b.setAttribute('aria-label', (on ? 'Hide' : 'Show') + ' the agents of ' + (nm ? nm.textContent : ''));
    g.hidden = !on;
  }

  /* ---------- reader aside ---------- */
  let rOpener = null;
  const reader = () => d.getElementById('pj-reader');
  function rOpen(o) {
    const r = reader(), body = r && pm7.$('[data-pj-rbody]', r);
    if (!body) return false;
    if (r.hidden) rOpener = d.activeElement;
    pm7.$('[data-pj-rt]', r).textContent = o.title || 'Reader';
    const open = pm7.$('[data-pj-ropen]', r), href = o.href != null ? o.href : o.src || o.url || '';
    open.hidden = !href;
    if (href) open.setAttribute('href', href);
    body.classList.toggle('pj-fpad', !o.src);
    body.classList.toggle('x-cl', !o.src && o.cl !== false);  // cl: false = a dt fragment (its own .x-cl if any)
    if (o.src) {
      const f = d.createElement('iframe');
      f.title = o.title || 'Reader';
      f.src = o.src;
      body.replaceChildren(f);
    } else if (o.url) {
      body.replaceChildren(Object.assign(d.createElement('p'), { className: 'muted', textContent: 'Loading…' }));
      load(o.url, body);
    }
    r.hidden = false;
    pm7.layers.sync();
    pm7.$('[data-pj-rclose]', r).focus({ preventScroll: true });
    return true;
  }
  function rClose() {
    const r = reader();
    if (!r || r.hidden) return;
    const inside = r.contains(d.activeElement);
    r.hidden = true;
    pm7.$('[data-pj-rbody]', r).replaceChildren();
    pm7.$$('.p-gr.sel[data-pj-plan]').forEach((x) => x.classList.remove('sel'));
    pm7.layers.sync();
    if (inside && rOpener && d.contains(rOpener)) rOpener.focus({ preventScroll: true });
    rOpener = null;
  }
  const rIsOpen = () => { const r = reader(); return !!r && !r.hidden; };

  /* ---------- head actions ---------- */
  async function answer(r) {  // a POST answer's text: git JSON {ok, text} | <p class="ok">text</p> | ''
    const t = await r.text();
    if ((r.headers.get('content-type') || '').indexOf('json') >= 0) {
      try { const j = JSON.parse(t); return { ok: j.ok !== false, text: j.text || j.error || '' }; }
      catch (e) { return { ok: true, text: '' }; }
    }
    const p = new DOMParser().parseFromString(t, 'text/html').body, first = pm7.$('p', p) || p;
    return { ok: !pm7.$('.err, .bad', p), text: (first.textContent || '').replace(/\s+/g, ' ').trim() };
  }
  async function post(url, form, what, whole) {
    let r;
    try { r = await pm7.fetch(url, { form: form }); } catch (e) { r = null; }
    if (!r || !r.ok) { pm7.fail(what, r); return null; }
    const a = await answer(r);
    if (!a.ok) { pm7.toast(what + ': ' + (a.text || 'failed'), '', { error: true }); return null; }
    pm7.toast((whole ? a.text : a.text.split(/(?<=\.)\s/)[0]) || what + ': done');  // git: not its ref lines
    return a;
  }
  async function refreshHead() {  // after a git action: the stats line and the tiles from a fresh render
    let r;
    try { r = await pm7.fetch(window.location.pathname); } catch (e) { return; }
    if (!r || !r.ok) return;
    const doc = new DOMParser().parseFromString(await r.text(), 'text/html');
    ['#pj-head-stats', '#pj-main > .pj-sum'].forEach((sel) => {
      const now = d.querySelector(sel), fresh = doc.querySelector(sel);
      if (now && fresh) now.replaceWith(d.importNode(fresh, true));
    });
  }
  const GIT = { fetch: 'Fetch from gitdev', pull: 'Pull (fast-forward only)', push: 'Push to gitdev' };
  async function git(act, btn) {
    if (act === 'tab') { tab('git'); return; }
    if (act === 'remotes') { if (pm7.pjs) pm7.pjs.open('remotes', btn); return; }
    if (!GIT[act]) return;
    let p = { text: 'Fetch new commits from gitdev. Your files and branches do not change.', risk: 'safe' };
    if (act !== 'fetch') {
      let r;
      try { r = await pm7.fetch(base() + '/git/plan?action=' + act); } catch (e) { r = null; }
      if (!r || !r.ok) { pm7.fail('Could not check ' + act, r); return; }
      p = await r.json();
      if (p.blocked) { pm7.toast(p.text, '', { ms: 4000 }); return; }
    }
    const typed = p.risk === 'typed' && p.confirm ? p.confirm : undefined;
    const ok = await pm7.confirm({ title: GIT[act] + ' · ' + name(), body: p.text, ok: GIT[act].split(' (')[0],
      typed: typed, opener: btn });
    if (!ok) return;
    const url = act === 'pull' && p.guard === 'clash' ? base() + '/git/pull-stash' : base() + '/git/' + act;
    if (await post(url, typed ? { confirm: typed } : {}, GIT[act])) {
      const sec = d.getElementById('pj-t-git');
      if (sec && window.htmx) window.htmx.trigger(sec, 'pm7:refresh');
      refreshHead();
    }
  }
  async function restart(btn) {
    const n = btn.getAttribute('data-pj-restart');
    const ok = await pm7.confirm({ title: 'Restart the stack of ' + n + '?', ok: 'Restart', opener: btn,
      body: 'docker compose restarts every service of ' + n + '. Its pages are down for a few seconds.' });
    if (ok) post(base() + '/stack/restart', {}, 'Restart stack');
  }
  async function stop(btn) {
    const n = btn.getAttribute('data-pj-stop'), k = +btn.getAttribute('data-pj-stop-n') || 0;
    const ok = await pm7.confirm({ title: 'Stop ' + k + ' agent' + (k === 1 ? '' : 's') + ' of ' + n + '? Type PANIC',
      typed: 'PANIC', ok: 'Stop agents', danger: true, opener: btn,
      body: 'pm sends Esc, then Ctrl-C to the pane of every agent of ' + n + '. The panes stay open.' });
    if (ok && await post('/panic', { scope: 'project:' + n, confirm: 'PANIC' }, 'Stop agents')) {
      const a = d.getElementById('pj-agents');
      if (a && window.htmx) window.htmx.trigger(a, 'pm7:refresh');
    }
  }
  async function closeAll(btn) {  // the tmux tab's Close all: typed project name (V7-SEC-6)
    const n = btn.getAttribute('data-pj-closeall'), k = +btn.getAttribute('data-pj-closeall-n') || 0;
    const ok = await pm7.confirm({ title: 'Close ' + k + ' tmux session' + (k === 1 ? '' : 's') + ' of ' + n + '?',
      typed: n, ok: 'Close all', danger: true, opener: btn,
      body: 'Everything running in them stops; agents in them end and keep their Resume in Ended. Protected sessions '
        + 'and sessions with attached clients are skipped.' });
    if (ok && await post(base() + '/tmux/close-all', {}, 'Close all', true)) {
      const a = d.getElementById('pj-agents');  // its live fragment carries #pj-tmux out of band
      if (a && window.htmx) window.htmx.trigger(a, 'pm7:refresh');
    }
  }
  function launch(e, el) {
    if (!pm7.launch || typeof pm7.launch.open !== 'function') return;  // the link: /p/<name>/launch
    e.preventDefault();
    pm7.launch.open({ project: el.getAttribute('data-pj-launch') });
  }

  /* ---------- Files, Docs, Plans ---------- */
  const files = () => d.querySelector('[data-pj-files]');
  function filesUrl(path) {
    const f = files();
    return f.getAttribute('data-pj-files') + (path ? '?path=' + encodeURIComponent(path) : '');
  }
  function readme(atRoot) {
    const f = files(), box = f && pm7.$('[data-pj-freadme]', f);
    if (!box) return;
    box.hidden = !atRoot;
    if (box.hasAttribute('data-none')) box.hidden = true;  // no README here: no notice under every listing
    if (atRoot && !box.hasAttribute('data-loaded')) {
      box.setAttribute('data-loaded', '');
      load(f.getAttribute('data-pj-readme'), box).then(() => {
        if (!pm7.$('.pj-doc', box)) { box.setAttribute('data-none', ''); box.hidden = true; }
      });
    }
  }
  function openFiles(url) {
    const f = files(), body = f && pm7.$('[data-pj-fbody]', f);
    if (!body) return;
    const u = new URL(url, window.location.href), path = u.searchParams.get('path') || '';
    f.setAttribute('data-loaded', '');
    load(u.pathname + u.search, body).then(() => readme(!path && !u.searchParams.get('raw')));
    const p = hashState();
    if (p.get('tab') === 'files') {
      if (path) p.set('path', path); else p.delete('path');
      setHash(p);
    }
  }
  function filesFailed(body, x) {  // a path that is not there (any more), or no answer: say so, link the root
    const st = x ? x.status : 0, a = pm7.el('a', '', 'Back to the project root'), n = pm7.el('span', 'n');
    a.href = filesUrl('');
    a.setAttribute('data-pj-in', '');
    n.append(st === 404 ? 'This file or folder is not there. ' : 'Could not load the files' + (st ? ' (HTTP ' + st
      + '). ' : '. '), a);
    const line = pm7.el('p', 'p-status');
    line.setAttribute('role', 'status');
    line.append(pm7.el('span', 'w bad', st === 404 ? 'Not found' : 'Not loaded'), n);
    body.replaceChildren(line);
    readme(false);
  }
  ['htmx:responseError', 'htmx:sendError'].forEach((ev) => d.addEventListener(ev, (e) => {
    const t = e.detail && e.detail.target;
    if (t && t.matches && t.matches('[data-pj-files] [data-pj-fbody]')) filesFailed(t, e.detail.xhr);
  }));
  function pressDoc(b) {
    pm7.$$('button[data-pj-doc]', b.parentNode).forEach((x) => x.setAttribute('aria-pressed', x === b ? 'true'
      : 'false'));
  }
  async function firstDoc() {  // README, else the first document that exists (pm.web.dt.project.docs: lazy)
    const box = d.querySelector('[data-pj-docb][data-pj-lazy]');
    if (!box || box.hasAttribute('data-loaded')) return;
    box.setAttribute('data-loaded', '');
    const all = pm7.$$('button[data-pj-doc]').filter((b) => !b.hasAttribute('data-pj-miss')
      && !b.hasAttribute('data-pj-list'));
    for (const b of all) {
      pressDoc(b);
      await load(b.getAttribute('data-pj-doc'), box);
      if (pm7.$('.pj-doc', box)) return;
    }
  }
  d.addEventListener('pm7:pj-tab', (e) => {
    const f = files();
    if (e.detail.key === 'files' && f && !f.hasAttribute('data-loaded')) openFiles(filesUrl(hashState().get('path')));
    if (e.detail.key === 'docs') firstDoc();
  });
  function selRow(row) {  // the row shown in the reader is .sel (PARTS §6.4)
    pm7.$$('.p-gr.sel[data-pj-plan]').forEach((r) => r.classList.toggle('sel', r === row));
    if (row) row.classList.add('sel');
  }
  function plan(row) {
    const nm = row.querySelector('.gc-nm .nm');
    rOpen({ title: nm ? nm.textContent : 'Plan', src: row.getAttribute('data-pj-plan'),
      href: row.getAttribute('data-href') });
    selRow(row);
  }
  d.addEventListener('pj:plan', (e) => { const r = e.target.closest('[data-pj-plan]'); if (r) plan(r); });

  /* ---------- Docs › Skills: Local | Global, a row opens its SKILL.md in the reader ---------- */
  function scope(b) {
    const box = b.closest('[data-pjk]'), key = b.getAttribute('data-pjk-scope');
    pm7.$$('[data-pjk-scope]', box).forEach((x) => x.setAttribute('aria-pressed', x === b ? 'true' : 'false'));
    pm7.$$('[data-pjk-grp]', box).forEach((g) => { g.hidden = g.getAttribute('data-pjk-grp') !== key; });
  }
  function skill(url, title) { rOpen({ title: title || 'Skill', url: url, href: '', cl: false }); }
  d.addEventListener('pj:skill', (e) => {
    const r = e.target.closest('[data-pj-skill]');
    if (r) skill(r.getAttribute('data-pj-skill'), r.getAttribute('data-pj-skill-t'));
  });
  function docOpen(key) {  // Docs at one choice (#view=<key>); false when it is hidden
    const b = d.querySelector('button[data-pj-doc$="/view/' + CSS.escape(key) + '"]');
    const box = d.querySelector('[data-pj-docb]');
    if (!b || !box) return false;
    box.setAttribute('data-loaded', '');  // not the lazy first document
    tab('docs');
    pressDoc(b);
    return load(b.getAttribute('data-pj-doc'), box);
  }

  /* ---------- the classic UI's hash links ----------
     #browse[=path] #view=key #skill=s/src/n #issues #backlog #issue=N (the core's redirects of /p/<n>/files,
     /view/<key>, /skill/...; Save as issue's "Open it"; search hits): open the dt tab, the hash becomes #tab=... */
  function classic() {
    const p = hashState(), get = (k) => p.get(k) || '';
    if (p.has('browse')) {
      const path = get('browse'), f = files(), had = !!f && f.hasAttribute('data-loaded');
      setHash(new URLSearchParams(path ? { tab: 'files', path: path } : { tab: 'files' }));
      // a first show loads the hash's path itself (pm7:pj-tab): only an already loaded listing needs a new load
      if (tab('files', true) && had) openFiles(filesUrl(path));
      return true;
    }
    if (p.has('view')) {
      const key = get('view');
      if (key === 'dirty') return tab('git');
      if (!docOpen(key)) tab('docs');
      return true;
    }
    if (p.has('skill')) {
      const parts = get('skill').split('/').filter(Boolean);
      if (!docOpen('skills') || parts.length !== 3) { tab('docs'); return true; }
      skill(base() + '/skill/' + parts.map(encodeURIComponent).join('/'), parts[2]);
      return true;
    }
    const issue = /^\d+$/.test(get('issue')) ? +get('issue') : 0;
    if (!issue && !p.has('issues') && !p.has('backlog')) return false;
    if (!tab('issues')) {  // the Issues tab is hidden here: the Backlog page
      window.location.assign('/backlog?tab=issues&project=' + encodeURIComponent(name()));
      return true;
    }
    if (issue) {  // after every ready hook: backlog-reader.js sets pm7.backlog.reader
      setTimeout(() => { if (pm7.backlog && pm7.backlog.reader) pm7.backlog.reader(name(), issue); }, 0);
    }
    return true;
  }

  /* ---------- clicks ---------- */
  d.addEventListener('click', (e) => {
    const t = e.target;
    if (!t || !t.closest || !lay() || e.defaultPrevented) return;
    const L = (s) => t.closest(s);
    let el;
    if ((el = L('[data-pj-launch]'))) { if (plain(e)) launch(e, el); return; }
    if ((el = L('[data-pj-goto]'))) { e.preventDefault(); tab(el.getAttribute('data-pj-goto')); return; }
    if (L('[data-pj-navc], [data-pj-navo]')) { e.preventDefault(); toggleNav(false); return; }
    if (L('[data-pj-navx]')) { e.preventDefault(); closeNav(); return; }
    if ((el = L('[data-pj-tc]'))) { e.preventDefault(); fold(el); return; }
    if ((el = L('a[data-pj-ag]')) && plain(e) && pm7.aside && pm7.aside.open) {
      e.preventDefault();
      closeNav();
      pm7.aside.open(el.getAttribute('data-pj-ag'), { focus: false });
      return;
    }
    if ((el = L('[data-pj-restart]'))) { e.preventDefault(); restart(el); return; }
    if ((el = L('[data-pj-stop]'))) { e.preventDefault(); if (!el.disabled) stop(el); return; }
    if ((el = L('[data-pj-closeall]'))) { e.preventDefault(); closeAll(el); return; }
    if ((el = L('[data-pjk-scope]'))) { e.preventDefault(); scope(el); return; }
    if ((el = L('[data-pj-rclose]'))) { e.preventDefault(); rClose(); return; }
    if ((el = L('[data-pj-files] a[data-pj-in]')) && plain(e)) { e.preventDefault(); openFiles(el.href); return; }
    if ((el = L('button[data-pj-doc]'))) {
      e.preventDefault();
      pressDoc(el);
      load(el.getAttribute('data-pj-doc'), d.querySelector('[data-pj-docb]'));
    }
  });
  d.addEventListener('pj:git', (e) => { if (e.detail) git(e.detail.item, e.target); });

  pm7.pj = { tab: tab, refresh: refreshHead, reader: { open: rOpen, close: rClose, isOpen: rIsOpen },
    hash: { get: hashState, set: setHash },
    nav: { toggle: toggleNav, open: openNav, close: closeNav } };
  pm7.ready(() => {
    if (!lay()) return;
    pm7.layers.soft({ rank: 40, open: navOpen, close: closeNav, scrim: true });
    pm7.layers.soft({ rank: 60, open: rIsOpen, close: rClose, scrim: false });
    if (pm7.keys) {
      pm7.keys.register({ id: 'project', group: 'Project', active: () => !!lay() },
        [{ id: 'pjnav', label: 'Project sidebar: collapse / show', keys: [NAVK], run: () => toggleNav(true) }]);
      pm7.keys.fill(lay());
    }
    window.addEventListener('resize', pm7.debounce(() => { if (wide()) closeNav(); expanded(); }, 100));
    expanded();
    const want = hashState().get('tab'), cur = d.querySelector('#pj-main .p-tabs [aria-selected=true]');
    if (classic()) return;
    if (!(want && tab(want, true)) && cur) shown(cur.getAttribute('data-p-tab'), true);
  });
})();
