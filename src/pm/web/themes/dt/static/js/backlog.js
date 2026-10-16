/* dt Backlog (docs/themes/dt/PRD.md §6.5): the Issues / TODO component on the Backlog page and on the project
   view's Issues / TODO tabs (README O7). Markup: templates/backlog/*.html, _issue_detail.html,
   project/_tab_{issues,todo}.html.
   Parts JS does the rest: table.js (folds), listkeys.js (J K, row click / ↵ = event bl:open, X = bl:select,
   E = ⋯ item "state" / bl:tick), filter.js, parts.js (⋯ menus: their POSTs with confirm, toast and pm7:refresh).
   This file adds only the Backlog's own behaviour; every write is a core tracker route (POST with CSRF, pm7.fetch),
   and every result toast comes after the server said OK (V7-CODE-5):
   - selection: checkboxes [data-bl-sel], bl:select (⋯ Select, X); kept in memory per component across swaps; rows in
     folded groups are never selected (folding a group drops its rows); the bulk bar [data-bl-bulkbar] shows the count
     and runs Done / Dispatch / Move as one POST per issue, then reports per item.
   - issue rows: [data-bl-run=<⋯ item id>] runs that ⋯ item (the visible action); bl:dispatch (confirm, then POST
     /p/{n}/issues/{num}/dispatch), bl:move (confirm with a project select, POST .../move {to}), bl:open (reader),
     bl:edit (the reader with its Edit form open).
   - TODO rows: [data-bl-tick] / bl:tick (POST /p/{n}/todo/toggle {line, text}), bl:tdispatch / [data-bl-run=dispatch]
     (pm7.launch.open({project, todo: index}) when the Launch dialog is there, else confirm + POST /p/{n}/dispatch);
     bl:tedit (Edit task) is js/backlog-add.js's.
   - R (pm7.keys.register, group "This list", while a Backlog list shows): runs the focused row's ⋯ item "edit".
   - dialogs: the Add issue / Add task / Edit task dialogs are js/backlog-add.js, the reader #bl-rd (GET
     /p/{n}/issues/{num}, its actions, Edit, Delete, Add note) is js/backlog-reader.js; both load after this file and
     use pm7.backlog.lib (one post helper for the three files).
   - the Backlog page keeps the address bar on the component's URL (data-bl-url) after a swap; the project view's tabs
     load their component when the frame shows them (pm7:pj-tab {key} on document) or when shown at first paint.
   Globals: pm7.backlog = {refresh(id?), reader(project, num, opener, {edit}) (set by backlog-reader.js), add (set by
   backlog-add.js), lib: {post, json, copy, dispatch, issueUrl, refreshAll, projects} (for backlog-reader.js and
   backlog-add.js only)}. Runs once however many partials load it. */
(function () {
  'use strict';
  const pm7 = window.pm7, d = document;
  if (!pm7 || pm7.backlog) return;
  const ROW = '.p-gr[data-row-id]';
  const sel = new Map();            // component id -> Set(row id)

  const enc = encodeURIComponent;
  const comp = (el) => (el && el.closest ? el.closest('.bl-c') : null);
  const rowOf = (el) => (el && el.closest ? el.closest(ROW) : null);
  const plural = (n, w) => n + ' ' + w + (n === 1 ? '' : 's');
  const folded = (r) => r.hidden || !!r.closest('.p-gg.fold, [hidden]');
  function info(r) {
    return { id: r.getAttribute('data-row-id'), p: r.getAttribute('data-bl-p') || '', n: r.getAttribute('data-bl-n'),
      done: r.classList.contains('bl-done'), agent: !!r.querySelector('.gc-dot .dot'), row: r };
  }
  const issueUrl = (i) => '/p/' + enc(i.p) + '/issues/' + enc(i.n);
  function refresh(c) {
    const el = typeof c === 'string' ? d.getElementById(c) : c;
    if (el && el.hasAttribute('hx-get') && window.htmx) window.htmx.trigger(el, 'pm7:refresh');
  }
  function refreshAll() { refresh('bl-issues'); refresh('bl-todo'); }
  function projects(c) {
    try { const v = JSON.parse((c && c.getAttribute('data-bl-projects')) || '[]'); return Array.isArray(v) ? v : []; }
    catch (e) { return []; }
  }
  const el = pm7.el;  // core.js: el(tag, cls, text)
  function copy(text) {
    const ok = () => pm7.toast('Copied'), bad = () => pm7.toast('Copy failed', '', { error: true });
    if (navigator.clipboard) navigator.clipboard.writeText(text).then(ok, bad); else bad();
  }

  /* one POST; {ok, r, why}: why = the server's message for a failed answer (pm7.why) */
  async function post(url, form, what, hx) {
    let r = null;
    try { r = await pm7.fetch(url, { form: form || {}, headers: hx ? { 'HX-Request': 'true' } : undefined }); }
    catch (e) { r = null; }
    if (r && r.ok) return { ok: true, r: r };
    return { ok: false, r: r, why: await pm7.why(r, what || 'Failed') };
  }
  async function json(r) { try { return await r.clone().json(); } catch (e) { return {}; } }

  /* ---------- selection and the bulk bar ---------- */
  function set(c) { if (!sel.has(c.id)) sel.set(c.id, new Set()); return sel.get(c.id); }
  function paint(c) {
    if (!c || !c.id) return;
    const s = set(c), rows = pm7.$$(ROW, c), ids = new Set(rows.map((r) => r.getAttribute('data-row-id')));
    s.forEach((id) => { if (!ids.has(id)) s.delete(id); });
    rows.forEach((r) => {
      const on = s.has(r.getAttribute('data-row-id')), ck = r.querySelector('[data-bl-sel]');
      r.classList.toggle('bl-on', on);
      if (ck) ck.checked = on;
    });
    const bar = c.querySelector('[data-bl-bulkbar]'), n = bar && bar.querySelector('[data-bl-n]');
    if (bar) bar.hidden = !s.size;
    if (n) n.textContent = s.size + ' selected';
    c.classList.toggle('bl-selmode', s.size > 0);
  }
  function select(r, on) {
    const c = comp(r);
    if (!c || !r.querySelector('[data-bl-sel]')) return false;
    const s = set(c), id = r.getAttribute('data-row-id');
    const want = on === undefined ? !s.has(id) : !!on;
    if (want && folded(r)) return false;
    if (want) s.add(id); else s.delete(id);
    paint(c);
    return true;
  }
  const chosen = (c) => pm7.$$(ROW, c).filter((r) => set(c).has(r.getAttribute('data-row-id')) && !folded(r));

  /* run fn(issue) for each, one at a time; report per item; drop the done ones from the selection */
  async function each(c, items, fn, verb, skipped) {
    let ok = 0;
    const bad = [];
    for (const i of items) {
      const res = await fn(i);
      if (res.ok) { ok++; set(c).delete(i.id); } else bad.push('#' + i.n + ' ' + i.p + ': ' + res.why);
    }
    const skip = skipped && skipped.length ? ' · skipped ' + skipped.join(', ') : '';
    if (bad.length) pm7.toast(ok + ' ' + verb + ', ' + bad.length + ' failed (' + bad.join('; ') + ')' + skip, '',
      { error: true, ms: 8000 });
    else pm7.toast(plural(ok, 'issue') + ' ' + verb + skip);
    paint(c);
    refreshAll();
  }
  async function bulk(c, what, btn) {
    if (what === 'clear') { set(c).clear(); paint(c); return; }
    if (what === 'all') {
      pm7.$$(ROW, c).forEach((r) => {
        if (!folded(r) && r.querySelector('[data-bl-sel]')) set(c).add(r.getAttribute('data-row-id'));
      });
      paint(c);
      return;
    }
    const all = chosen(c).map(info);
    if (!all.length) return;
    if (what === 'move') { move(c, all, btn); return; }
    if (what === 'done') {
      const todo = all.filter((i) => !i.done), skip = all.filter((i) => i.done).map((i) => '#' + i.n + ' (done)');
      if (!todo.length) { pm7.toast('The selected issues are done already'); return; }
      const ok = await pm7.confirm({ title: 'Mark ' + plural(todo.length, 'issue') + ' done?',
        body: todo.map((i) => '#' + i.n + ' ' + i.p).join(', '), ok: 'Mark done', opener: btn });
      if (ok) each(c, todo, (i) => post(issueUrl(i) + '/done', {}, '#' + i.n), 'done', skip);
      return;
    }
    const go = all.filter((i) => !i.done && !i.agent);
    const skip = all.filter((i) => i.done || i.agent).map((i) => '#' + i.n + (i.done ? ' (done)' : ' (has an agent)'));
    if (!go.length) { pm7.toast('Nothing to dispatch: ' + skip.join(', ')); return; }
    const ok = await pm7.confirm({ title: 'Dispatch ' + plural(go.length, 'issue') + '?',
      body: 'Starts ' + plural(go.length, 'new agent') + ' (claude), one per issue: ' +
        go.map((i) => '#' + i.n + ' ' + i.p).join(', ') + '.', ok: 'Dispatch', opener: btn });
    if (ok) each(c, go, dispatchOne, 'dispatched', skip);
  }

  /* ---------- issue actions: Dispatch, Move ---------- */
  async function dispatchOne(i) {
    const res = await post(issueUrl(i) + '/dispatch', {}, 'Dispatch #' + i.n, true);
    if (!res.ok) return res;
    let html = '';
    try { html = await res.r.text(); } catch (e) { html = ''; }
    const m = /href="\/a\/([A-Za-z0-9_.-]+)"/.exec(html);
    return { ok: true, agent: m ? m[1] : '' };
  }
  async function dispatch(i, opener) {
    const ok = await pm7.confirm({ title: 'Dispatch #' + i.n + ' (' + i.p + ')?',
      body: 'Starts a new claude agent in ' + i.p + ' with the issue as its prompt.', ok: 'Dispatch', opener: opener });
    if (!ok) return false;
    const res = await dispatchOne(i);
    if (res.ok) pm7.toast('#' + i.n + ' dispatched' + (res.agent ? ' to ' + res.agent : ''));
    else pm7.toast(res.why, '', { error: true });
    refreshAll();
    return res.ok;
  }
  async function move(c, items, opener) {
    const from = new Set(items.map((i) => i.p));
    const dest = projects(c).filter((p) => !(from.size === 1 && from.has(p)));
    if (!dest.length) { pm7.toast('No other tracker project to move to', '', { error: true }); return; }
    const body = el('div'), lab = el('label', 'x-dlg-l', 'Move to '), pick = el('select', 'sel');
    dest.forEach((p) => { const o = el('option', '', p); o.value = p; pick.appendChild(o); });
    lab.appendChild(pick);
    body.append(el('p', '', 'The issue gets the next number there; its notes and images move with it.'), lab);
    const one = items.length === 1;
    const ok = await pm7.confirm({ title: one ? 'Move #' + items[0].n + ' (' + items[0].p + ') to another project?'
      : 'Move ' + items.length + ' issues to another project?', body: body, ok: 'Move', opener: opener });
    if (!ok) return;
    const to = pick.value, go = items.filter((i) => i.p !== to);
    const skip = items.filter((i) => i.p === to).map((i) => '#' + i.n + ' (already in ' + to + ')');
    const done = [];
    await each(c, go, async (i) => {
      const res = await post(issueUrl(i) + '/move', { to: to }, '#' + i.n);
      if (res.ok) { const j = await json(res.r); done.push('#' + i.n + ' → ' + to + ' #' + (j.num || '?')); }
      return res;
    }, 'moved to ' + to, skip);
    if (one && done.length) pm7.toast('Moved ' + done[0]);
  }

  /* ---------- TODO: tick, Dispatch ---------- */
  async function tick(r) {
    const done = r.classList.contains('bl-done'), p = r.getAttribute('data-bl-p') || '';
    const text = r.getAttribute('data-bl-text') || '';
    const res = await post('/p/' + enc(p) + '/todo/toggle', { line: r.getAttribute('data-bl-line'), text: text },
      done ? 'Reopen' : 'Tick');
    if (res.ok) pm7.toast((done ? 'Reopened: ' : 'Ticked: ') + (text.length > 60 ? text.slice(0, 59) + '…' : text));
    else pm7.toast(res.why, '', { error: true });
    refreshAll();
  }
  async function todoDispatch(r, opener) {
    const p = r.getAttribute('data-bl-p') || '', ix = r.getAttribute('data-bl-ix');
    if (ix == null) return;
    if (pm7.launch && typeof pm7.launch.open === 'function') {
      pm7.launch.open({ project: p, todo: Number(ix) });
      return;
    }
    const text = r.getAttribute('data-bl-text') || '';
    const ok = await pm7.confirm({ title: 'Dispatch this task?', body: 'Starts a new claude agent in ' + p +
      ' with the task as its prompt: ' + text, ok: 'Dispatch', opener: opener });
    if (!ok) return;
    const res = await post('/p/' + enc(p) + '/dispatch', { index: ix }, 'Dispatch', true);
    if (res.ok) pm7.toast('Task dispatched in ' + p); else pm7.toast(res.why, '', { error: true });
    refreshAll();
  }

  /* ---------- events ---------- */
  d.addEventListener('change', (e) => {
    const t = e.target;
    if (t.matches && t.matches('[data-bl-sel]')) {
      const r = rowOf(t);
      if (r && !select(r, t.checked)) t.checked = false;
    }
  });
  d.addEventListener('click', (e) => {
    const t = e.target;
    if (!t || !t.closest) return;
    let b = t.closest('[data-bl-run]');
    if (b) {
      const r = rowOf(b), id = b.getAttribute('data-bl-run');
      if (r && id === 'dispatch' && r.hasAttribute('data-bl-line')) todoDispatch(r, b);
      else if (r && pm7.menus) pm7.menus.run(r.querySelector('[data-menu]'), id);
      return;
    }
    if ((b = t.closest('[data-bl-tick]'))) { const r = rowOf(b); if (r) tick(r); return; }
    if ((b = t.closest('[data-bl-bulk]'))) {
      const c = comp(b);
      if (c) bulk(c, b.getAttribute('data-bl-bulk'), b);
    }
  });
  const on = (type, fn) => d.addEventListener(type, (e) => { const r = rowOf(e.target); if (r && comp(r)) fn(r, e); });
  on('bl:open', (r) => { const i = info(r); if (pm7.backlog.reader) pm7.backlog.reader(i.p, i.n, r); });
  on('bl:edit', (r) => { const i = info(r); if (pm7.backlog.reader) pm7.backlog.reader(i.p, i.n, r, { edit: true }); });
  on('bl:select', (r) => { if (!select(r)) pm7.toast('Unfold the group to select its rows'); });
  on('bl:dispatch', (r, e) => { dispatch(info(r), e.target); });
  on('bl:move', (r, e) => { move(comp(r), [info(r)], e.target); });
  on('bl:tick', (r) => { tick(r); });
  on('bl:tdispatch', (r, e) => { todoDispatch(r, e.target); });
  d.addEventListener('pm7:fold', (e) => {
    const g = e.target, c = comp(g);
    if (!c || (e.detail && e.detail.open)) return;
    pm7.$$(ROW, g).forEach((r) => set(c).delete(r.getAttribute('data-row-id')));
    paint(c);
  });

  /* ---------- swaps: selection back, address bar; the project view's lazy tabs ---------- */
  d.addEventListener('htmx:afterSettle', () => {
    pm7.$$('.bl-c[id]').forEach(paint);
    const c = d.querySelector('.bl-c[data-bl-scope=page][data-bl-url]');
    const u = c && c.getAttribute('data-bl-url');
    if (u && window.location.pathname === '/backlog' && window.location.pathname + window.location.search !== u) {
      window.history.replaceState(window.history.state, '', u);
    }
  });
  function lazy(c, force) {
    const sec = c && c.closest('[data-pj-tab]');
    if (!c || !window.htmx || !c.hasAttribute('data-bl-lazy') || (!force && sec && sec.hidden)) return;
    const url = c.getAttribute('data-bl-lazy');
    c.removeAttribute('data-bl-lazy');  // the loaded component has its own hx-get and refreshes itself
    window.htmx.ajax('GET', url, { target: c, swap: 'outerHTML' });
  }
  d.addEventListener('pm7:pj-tab', (e) => {
    const k = e.detail && e.detail.key;
    if (k === 'issues' || k === 'todo') lazy(d.querySelector('[data-pj-tab="' + k + '"] [data-bl-lazy]'), true);
  });
  pm7.ready(() => pm7.$$('[data-bl-lazy]').forEach((c) => lazy(c, false)));

  /* ---------- R: the focused row's ⋯ Edit (issue: the reader's Edit form; task: #bl-edt) ---------- */
  const lists = () => pm7.$$('.bl-c [data-list]').some((l) => l.getClientRects().length > 0);
  function editKey() {  // a disabled item (a TODO.md task) says why
    const r = pm7.list && pm7.list.row(), m = r && comp(r) && r.querySelector('[data-menu]');
    if (!m || !pm7.menus) return false;
    if (pm7.menus.run(m, 'edit')) return true;
    let it = null;
    try { it = JSON.parse(m.getAttribute('data-menu')).find((x) => x.id === 'edit'); } catch (e) { it = null; }
    if (it && it.disabled) pm7.toast('Cannot edit: ' + (it.why || 'not here'));
    return !!it;
  }
  pm7.ready(() => {
    if (pm7.keys && pm7.keys.register) {
      pm7.keys.register({ id: 'backlog', group: 'This list', active: lists },
        [{ id: 'bl-edit', label: 'Edit the row (issue or task)', keys: ['R'], run: editKey }]);
    }
  });

  pm7.backlog = { refresh: (id) => (id ? refresh(id) : refreshAll()), reader: null, add: null,
    lib: { post: post, json: json, copy: copy, dispatch: dispatch, issueUrl: issueUrl, refreshAll: refreshAll,
      projects: projects } };
})();
