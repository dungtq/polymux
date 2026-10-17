/* dt Backlog: BACKLOG.md files of projects that do not use the tracker (PRD.md §6.5; PARTS.md §15). Markup:
   backlog/_issues.html (rows [data-bl-fl] = the item's line, data-bl-text = its text, band button [data-bl-editfile])
   and backlog/_editor.html (form[data-bl-fe], the whole-file editor). Loaded after js/backlog.js wherever the Issues
   component or the editor is (backlog/_page.html, project/_tab_issues.html). Every write is a core route (POST with
   CSRF, pm7.fetch: a script call, so the core answers JSON); a result toast comes after the server said OK (V7-CODE-5).
   - rows: tick / reopen is the row's ⋯ item "state" (POST /backlog/toggle, parts.js); bl:open (click, ↵) and bl:fedit
     (⋯ Edit, R) ask for the new text in a dialog (pm7.confirm with a field) and POST /backlog/edit {project, line,
     text, new_text} (the core finds the line again by its text, or refuses: the file changed); [data-bl-editfile]
     opens the editor page.
   - editor: holds the core's edit lease while the page is open: POST /backlog/lease/acquire on load (409 = someone
     else holds it: reload, the page then shows it read-only), /renew every 60 s and when the tab shows again,
     /release on leave (keepalive); a Save keeps it (the core releases it). "Overwrite anyway" asks first.
   Globals: pm7.backlog.file = {edit(row, opener)}. Runs once however many partials load it. */
(function () {
  'use strict';
  const pm7 = window.pm7, d = document;
  if (!pm7 || !pm7.backlog || pm7.backlog.file) return;
  const el = pm7.el;
  const cut = (t, n) => (t.length > n ? t.slice(0, n - 1) + '…' : t);
  const fileRow = (t) => (t && t.closest ? t.closest('.bl-c .p-gr[data-bl-fl]') : null);

  /* ---------- an item's text ---------- */
  async function edit(r, opener) {
    const p = r.getAttribute('data-bl-p') || '', old = r.getAttribute('data-bl-text') || '';
    const input = el('input.x-in', { type: 'text', maxlength: 500, autocomplete: 'off', spellcheck: 'true',
      'aria-label': 'Item text' });
    input.value = old;
    const body = el('div', null, el('p.bl-note', null, p + '/BACKLOG.md: the line keeps its [ ] / [x] mark.'),
      el('label.bl-fl', null, el('span', null, 'Text'), input));
    const asked = pm7.confirm({ title: 'Edit the item', body: body, ok: 'Save', opener: opener || r });
    input.addEventListener('keydown', (e) => {
      if (e.key !== 'Enter' || e.isComposing) return;
      e.preventDefault();
      const ok = input.closest('dialog') && input.closest('dialog').querySelector('.ft .pri');
      if (ok) ok.click();
    });
    input.focus();
    input.select();
    if (!(await asked)) return;
    const text = input.value.replace(/\s+/g, ' ').trim();
    if (!text) { pm7.toast('The text is empty: nothing saved', '', { error: true }); return; }
    if (text === old) return;
    let res = null;
    try {
      res = await pm7.fetch('/backlog/edit', { form: { project: p, line: r.getAttribute('data-bl-fl'), text: old,
        new_text: text } });
    } catch (e) { res = null; }
    if (!(await pm7.reply(res)).ok) { pm7.fail('Save', res); return; }
    pm7.toast('Saved: ' + cut(text, 60));
    if (pm7.backlog) pm7.backlog.refresh('bl-issues');
  }

  d.addEventListener('bl:open', (e) => { const r = fileRow(e.target); if (r) edit(r, e.target); });
  d.addEventListener('bl:fedit', (e) => { const r = fileRow(e.target); if (r) edit(r, e.target); });
  d.addEventListener('click', (e) => {
    const b = e.target && e.target.closest ? e.target.closest('[data-bl-editfile]') : null;
    if (b) window.location.assign(b.getAttribute('data-bl-editfile'));
  });

  /* ---------- the whole-file editor: edit lease, Overwrite ---------- */
  function editor(form) {
    const lease = form.elements.lease, project = form.getAttribute('data-project') || '';
    const msg = form.querySelector('[data-bl-femsg]');
    let left = false;
    const say = (t) => { if (msg) { msg.textContent = t; msg.hidden = !t; } };
    const post = (what, keep) => pm7.fetch('/backlog/lease/' + what, { form: { project: project, lease: lease.value },
      keepalive: !!keep, cache: 'no-store' });
    function hold(id) {
      lease.value = id;
      try {
        const u = new URL(window.location.href);
        u.searchParams.set('lease', id);
        window.history.replaceState(window.history.state, '', u.pathname + u.search + u.hash);
      } catch (e) { /* the address bar keeps the old lease id: a reload takes a new lease */ }
    }
    async function acquire() {
      let r = null;
      try { r = await post('acquire'); } catch (e) { r = null; }
      if (r && r.status === 409) { window.location.reload(); return; }
      const a = await pm7.reply(r);
      if (!a.ok || !a.json || !a.json.lease) { say(await pm7.why(r, 'Could not take the edit lease')); return; }
      hold(a.json.lease);
      say('');
    }
    async function renew() {
      if (left || !lease.value) { if (!left) acquire(); return; }
      let r = null;
      try { r = await post('renew'); } catch (e) { r = null; }
      if (r && r.ok) { say(''); return; }
      if (r && r.status === 409) { acquire(); return; }
      say(await pm7.why(r, 'Could not renew the edit lease'));
    }
    function release() {
      if (left || !lease.value) return;
      left = true;
      post('release', true).catch(() => {});
    }
    acquire();
    window.setInterval(() => { if (!d.hidden) renew(); }, 60000);
    d.addEventListener('visibilitychange', () => { if (!d.hidden) renew(); });
    window.addEventListener('pagehide', (e) => { if (!e.persisted) release(); });
    window.addEventListener('pageshow', (e) => { if (e.persisted) { left = false; renew(); } });
    form.addEventListener('submit', () => { left = true; });
  }
  d.addEventListener('click', async (e) => {
    const b = e.target && e.target.closest ? e.target.closest('[data-bl-feforce]') : null;
    if (!b || b.hasAttribute('data-ok')) return;
    e.preventDefault();
    const ok = await pm7.confirm({ title: 'Overwrite BACKLOG.md with your text?', danger: true, ok: 'Overwrite',
      body: 'The changes made to the file since you opened it are lost (the differences are listed above).',
      opener: b });
    if (!ok) return;
    b.setAttribute('data-ok', '');
    b.click();
  });
  pm7.backlog.file = { edit: edit };
  pm7.ready(() => {
    const f = d.querySelector('form[data-bl-fe]');
    if (f && !f.hasAttribute('data-readonly')) editor(f);
    const ta = f && f.querySelector('textarea');
    if (ta && !ta.readOnly) ta.focus({ preventScroll: true });
  });
})();
