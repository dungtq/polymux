/* dt tmux page (PRD.md §6.6). Markup: templates/tmux.html, _tmux_list.html, _tmux_session_rows.html; parts
   JS: table.js (row details = a session's windows, loaded from GET /tmux/{sid}/live), listkeys.js (J K ↵ O X on the
   sessions list "tmux" and the windows lists "tmux-win"), filter.js, parts.js (⋯ menus, their POSTs, typed confirms
   and toasts). This file adds only what is the tmux page's own:
   - terminal: event tm:term (⋯ "Open terminal", O / ↵ on a window row) and a[data-tm-term-link] (window name) open
     the row's read-only terminal (data-tm-term = /term/sN?w=wM) as a tab: pm7.tabs.open('term', '$N', href).
   - agents: a[data-tm-agent] opens that agent's chat in the aside (pm7.aside.open; phones: a tab).
   - launch: event tm:launch (session ⋯ "Launch an agent here") opens the Launch dialog for the session's project
     (pm7.launch.open({project})), else goes to that project's Launch page (data-tm-launch).
   - New session: [data-tm-new] opens dialog#tm-new (pm7.dialog); its form posts /tmux/session/new (name, cwd) with
     pm7.fetch; the toast comes after the server said OK, an error stays in the dialog ([data-tm-err]).
   - the project view's tmux tab (#pj-tmux[data-tm-rows], the same rows): the terminal, agent and launch hooks too.
   Runs once (guard); global pm7.tm = {term(href)} (open a /term/sN href as a tab).
   - live swaps: #tm-list swaps itself (outerHTML; #pj-tmux comes out of band); the open details' windows are
     copied into the fresh list until their fresh copy arrives (table.js loads it again), so a refresh never flashes
     an empty panel. */
(function () {
  'use strict';
  const pm7 = window.pm7, d = document;
  if (!pm7 || pm7.tm) return;  // once, however many pages / partials load it (the project view's tmux tab too)
  pm7.tm = { term: (href) => openTerm(href) };
  const LIST = '#tm-list', ROWS = '#tm-list, [data-tm-rows]';  // [data-tm-rows]: the project view's tmux tab

  const rowOf = (el) => (el && el.closest ? el.closest('[data-row-id]') : null);
  function refresh() { const b = d.querySelector(LIST); if (b && window.htmx) window.htmx.trigger(b, 'pm7:refresh'); }
  function plain(e) { return !(e.button || e.metaKey || e.ctrlKey || e.shiftKey || e.altKey); }

  /* ---------- terminal tab, chat aside, Launch ---------- */
  function openTerm(href) {
    const m = /^\/term\/s(\d+)(?:[?#]|$)/.exec(href || '');
    if (!m) return false;
    if (pm7.tabs && pm7.tabs.open) pm7.tabs.open('term', '$' + m[1], href);
    else window.location.assign(href);
    return true;
  }
  d.addEventListener('tm:term', (e) => {
    const r = rowOf(e.target);
    if (r && !openTerm(r.getAttribute('data-tm-term'))) pm7.toast('No terminal for this session', '', { error: true });
  });
  d.addEventListener('tm:launch', (e) => {
    const r = rowOf(e.target), project = r && r.getAttribute('data-tm-project');
    if (!project) return;
    if (pm7.launch && pm7.launch.open) pm7.launch.open({ project: project });
    else if (r.getAttribute('data-tm-launch')) window.location.assign(r.getAttribute('data-tm-launch'));
  });
  d.addEventListener('click', (e) => {
    const t = e.target;
    if (!t || !t.closest || e.defaultPrevented || !t.closest(ROWS) || !plain(e)) return;
    const term = t.closest('a[data-tm-term-link]');
    if (term) { if (openTerm(term.getAttribute('href'))) e.preventDefault(); return; }
    const ag = t.closest('a[data-tm-agent]');
    if (ag && pm7.aside && pm7.aside.open) {
      e.preventDefault();
      pm7.aside.open(ag.getAttribute('data-tm-agent'), { focus: false });
    }
  });

  /* ---------- New session dialog ---------- */
  const dlg = () => d.getElementById('tm-new');
  function say(form, text) {
    const p = pm7.$('[data-tm-err]', form);
    if (!p) return;
    p.textContent = text || '';
    p.hidden = !text;
  }
  d.addEventListener('click', (e) => {
    const b = e.target && e.target.closest ? e.target.closest('[data-tm-new]') : null;
    const dl = dlg();
    if (!b || !dl) return;
    e.preventDefault();
    say(pm7.$('form', dl), '');
    pm7.dialog(dl, { opener: b });
  });
  let busy = false;
  d.addEventListener('submit', async (e) => {
    const form = e.target;
    if (!form || !form.matches || !form.matches('form[data-tm-newform]')) return;
    e.preventDefault();
    if (busy) return;
    const name = form.elements.name;
    if (!form.checkValidity()) {
      say(form, name && !name.validity.valid ? 'Name: letters, digits, dot, - and _ (not - first), up to 64.'
        : 'Check the folder.');
      if (name) name.focus();
      return;
    }
    busy = true;
    say(form, '');
    let r = null;
    try { r = await pm7.fetch(form.getAttribute('action'), { form: new URLSearchParams(new FormData(form)) }); }
    catch (err) { r = null; }
    busy = false;
    if (!r || !r.ok) { say(form, await pm7.why(r, 'Could not create the session')); return; }
    const n = name ? name.value : '';
    form.reset();
    pm7.layers.close(dlg());
    pm7.toast('Created session ' + n);
    refresh();
  });

  /* ---------- live swaps: keep the open windows until their fresh copy arrives ----------
     #tm-list swaps itself; the project view's #pj-tmux comes out of band with #pj-agents' live fragment */
  const BOXES = ['tm-list', 'pj-tmux'];
  let kept = null;
  function before(e) {
    const t = e.detail && e.detail.target;
    if (!t || BOXES.indexOf(t.id) < 0) return;
    kept = { id: t.id, map: new Map() };
    pm7.$$('.tm-list > .p-det:not([hidden]) > .p-detc', t).forEach((c) => {
      if (c.childElementCount) kept.map.set(c.parentNode.getAttribute('data-p-det-for'), c.innerHTML);
    });
  }
  function after() {
    const k = kept, b = k && d.getElementById(k.id);
    kept = null;
    if (!k || !b) return;
    k.map.forEach((html, id) => {
      const det = b.querySelector('.tm-list > .p-det[data-p-det-for="' + CSS.escape(id) + '"]');
      const c = det && pm7.$('.p-detc', det);
      if (c && !c.childElementCount) c.innerHTML = html;
    });
  }
  ['htmx:beforeSwap', 'htmx:oobBeforeSwap'].forEach((n) => d.addEventListener(n, before));
  ['htmx:afterSwap', 'htmx:oobAfterSwap'].forEach((n) => d.addEventListener(n, after));
})();
