/* dt Backlog: the Add issue (#bl-add), Add task (#bl-addt) and Edit task (#bl-edt) dialogs (PRD.md §6.5; PARTS.md
   §15). Markup: backlog/_dlg_issue.html, backlog/_dlg_task.html. Loaded right after js/backlog.js wherever one of
   them is (backlog/_page.html, project/_tab_issues.html, project/_tab_todo.html); uses its pm7.backlog.lib (one post
   helper, the lists' refresh, a component's tracker projects). Every write is a core tracker route (POST with CSRF,
   pm7.fetch); a result toast comes only after the server said OK (V7-CODE-5), a refusal stays in the form.
   - Add issue: images from the picker, or pasted / dropped anywhere in the form (pm7.attach.wire; images only), one
     pm7.attach chip each, uploaded on Add (POST /p/{n}/image), then POST /p/{n}/issues {title, body, images}.
     "One issue per line" ([data-bl-manyon]): the Titles box takes the place of Title, Details and images; each
     non-empty line is one POST, in order, and one toast follows ("Added 3 issues: #7–#9 to pm"); a failure stops
     there: the lines not added stay in the box, the reason goes to the form and an error toast (pm7.fail).
     "Add another" ([data-bl-more]): a success clears the fields and keeps the dialog open (project, both options).
   - Add task: POST /p/{n}/todo/add {text, section}; 409 = not a tracker project: the dialog says so and offers the
     pmctl line to copy.
   - Edit task: event bl:tedit on a TODO row (its ⋯ Edit, or R through backlog.js): POST /p/{n}/todo/edit {num, text}
     (num = the row's data-bl-line, the tracker item number); an unchanged text just closes the dialog.
   Globals: pm7.backlog.add = {open(kind, component, opener), task(row, opener)}. Runs once however many partials
   load it. */
(function () {
  'use strict';
  const pm7 = window.pm7, d = document;
  if (!pm7 || !pm7.backlog || !pm7.backlog.lib || pm7.backlog.add) return;
  const B = pm7.backlog.lib, el = pm7.el, enc = encodeURIComponent;
  const wired = new WeakSet();
  let files = [];  // the Add issue dialog's images, uploaded on Add

  const cut = (t, n) => (t.length > n ? t.slice(0, n - 1) + '…' : t);
  function say(f, text) {
    const err = f.querySelector('[data-bl-ferr]');
    err.textContent = text;
    err.hidden = !text;
  }

  /* ---------- Add issue ---------- */
  function fill(dl, c) {
    const s = dl.querySelector('[data-bl-fproj]'), row = dl.querySelector('[data-bl-fprow]');
    const list = B.projects(c), want = (c && c.getAttribute('data-bl-project')) || s.value || '';
    s.replaceChildren(...list.map((p) => { const o = el('option', '', p); o.value = p; return o; }));
    if (list.indexOf(want) >= 0) s.value = want;
    if (row) row.hidden = !!c && c.getAttribute('data-bl-scope') === 'project';
  }
  function chips(dl) {  // one pm7.attach chip per file; × drops the file
    const box = dl.querySelector('[data-bl-chips]');
    box.replaceChildren();
    files.forEach((f) => { pm7.attach.chip(box, f.name || 'image', () => { files = files.filter((x) => x !== f); }); });
  }
  const many = (f) => !!(f.querySelector('[data-bl-manyon]') || {}).checked;
  const first = (f) => (many(f) ? f.elements.lines : f.elements.title);
  function paint(f) {  // "One issue per line": Titles in place of Title, Details and images
    const on = many(f);
    pm7.$$('[data-bl-one]', f).forEach((n) => { n.hidden = on; });
    f.querySelector('[data-bl-many]').hidden = !on;
    f.querySelector('[data-bl-fok]').textContent = on ? 'Add issues' : 'Add issue';
  }
  function mode(f) {  // the option flipped: carry the text over when the other box is empty
    const one = f.elements.title, lines = f.elements.lines;
    if (many(f) && !lines.value.trim()) lines.value = one.value.trim();
    else if (!many(f) && !one.value.trim()) one.value = (lines.value.split('\n').find((l) => l.trim()) || '').trim();
    paint(f);
    say(f, '');
    first(f).focus();
  }
  function take(f, list) {  // picked, pasted or dropped files: images only, one chip each
    const imgs = Array.from(list || []).filter((x) => /^image\//.test(x.type));
    if (!imgs.length) return;
    if (many(f)) { say(f, 'Images go with one issue: turn off "One issue per line" to attach them.'); return; }
    files = files.concat(imgs);
    chips(f.closest('dialog'));
  }
  function openAdd(kind, c, opener) {
    const dl = d.getElementById(kind === 'task' ? 'bl-addt' : 'bl-add');
    if (!dl) return;
    const f = dl.querySelector('form');
    fill(dl, c);
    say(f, '');
    if (kind === 'task') fallback(f, '');
    else {
      paint(f);
      if (!wired.has(f)) { wired.add(f); pm7.attach.wire(f, (fl) => take(f, fl)); }
    }
    pm7.dialog(dl, { opener: opener, focus: kind === 'task' ? f.elements.text : first(f) });
  }
  function added(f) {  // a success: clear the fields; "Add another" keeps the dialog, the project and the options
    const dl = f.closest('dialog');
    f.elements.title.value = f.elements.body.value = f.elements.lines.value = '';
    files = [];
    chips(dl);
    B.refreshAll();
    if (f.querySelector('[data-bl-more]').checked) first(f).focus();
    else dl.close();
  }
  async function addIssue(f) {
    const p = f.elements.project.value, title = f.elements.title.value.trim(), ok = f.querySelector('[data-bl-fok]');
    if (!p) { say(f, 'Pick a project.'); return; }
    if (many(f)) { addMany(f, p, ok); return; }
    if (!title) { say(f, 'The title is required.'); f.elements.title.focus(); return; }
    say(f, '');
    ok.disabled = true;
    try {
      const paths = [];
      for (const file of files) paths.push(await pm7.attach.upload('/p/' + enc(p), file));
      const res = await B.post('/p/' + enc(p) + '/issues', { title: title, body: f.elements.body.value,
        images: paths.join('\n') }, 'Add issue');
      if (!res.ok) throw new Error(res.why);
      const j = await B.json(res.r);
      pm7.toast('Added #' + (j.num || '?') + ' to ' + p);
      added(f);
    } catch (e) {
      say(f, String(e && e.message ? e.message : e));
    } finally {
      ok.disabled = false;
    }
  }
  function nums(list) {  // [7, 8, 9] -> "#7–#9"; not consecutive -> "#7, #9"
    const run = list.every((n, i) => i === 0 || Number(n) === Number(list[i - 1]) + 1);
    return run && list.length > 2 ? '#' + list[0] + '–#' + list[list.length - 1] : list.map((n) => '#' + n).join(', ');
  }
  async function addMany(f, p, ok) {  // one POST per non-empty line, in order; stop at the first refusal
    const ta = f.elements.lines, lines = ta.value.split('\n').map((l) => l.trim()).filter(Boolean);
    if (!lines.length) { say(f, 'Write one title per line.'); ta.focus(); return; }
    say(f, '');
    ok.disabled = true;
    const got = [];
    let i = 0, res = null;
    try {
      for (; i < lines.length; i++) {
        res = await B.post('/p/' + enc(p) + '/issues', { title: lines[i], body: '', images: '' }, 'Add issue');
        if (!res.ok) break;
        got.push((await B.json(res.r)).num || '?');
      }
    } finally {
      ok.disabled = false;
    }
    if (i < lines.length) {
      ta.value = lines.slice(i).join('\n');
      const done = got.length ? 'Added ' + nums(got) + '; ' : '';
      say(f, await pm7.fail(done + 'stopped at "' + cut(lines[i], 40) + '"', res && res.r));
      if (got.length) B.refreshAll();
      ta.focus();
      return;
    }
    pm7.toast('Added ' + (got.length === 1 ? '' : got.length + ' issues: ') + nums(got) + ' to ' + p);
    added(f);
  }

  /* ---------- Add task ---------- */
  function sh(s) { return /^[\w@%+=:,./-]+$/.test(s) ? s : "'" + s.replace(/'/g, "'\\''") + "'"; }
  function cmd(f) {
    const p = f.elements.project.value, sec = f.elements.section.value.trim(), text = f.elements.text.value.trim();
    f.elements.cmd.value = 'pmctl --project ' + sh(p || '<project>') + ' todo add ' +
      (sec ? '--section ' + sh(sec) + ' ' : '') + sh(text || '<task>');
    return !!(p && text);
  }
  function fallback(f, p) {  // p = the project the core refused (409: not a tracker project); '' hides the fallback
    const box = f.querySelector('[data-bl-tfall]');
    box.hidden = !p;
    f.querySelector('[data-bl-tcopy]').hidden = !p;
    if (p) {
      f.querySelector('[data-bl-tnote]').textContent = 'pm adds tasks only to tracker projects; ' + p +
        ' keeps its tasks in TODO.md. Add the line to TODO.md, or import the project (pmctl import) and run:';
      cmd(f);
    }
    say(f, '');
  }
  async function addTask(f) {
    const p = f.elements.project.value, text = f.elements.text.value.trim(), ok = f.querySelector('[data-bl-fok]');
    if (!p) { say(f, 'Pick a project.'); return; }
    if (!text) { say(f, 'Write the task.'); f.elements.text.focus(); return; }
    say(f, '');
    ok.disabled = true;
    try {
      const res = await B.post('/p/' + enc(p) + '/todo/add', { text: text, section: f.elements.section.value.trim() },
        'Add task');
      if (res.r && res.r.status === 409) { fallback(f, p); return; }
      if (!res.ok) { say(f, res.why); return; }
      pm7.toast('Added the task to ' + p);
      f.reset();
      f.closest('dialog').close();
      pm7.backlog.refresh('bl-todo');
    } finally {
      ok.disabled = false;
    }
  }

  /* ---------- Edit task ---------- */
  function task(r, opener) {
    const dl = d.getElementById('bl-edt'), f = dl && dl.querySelector('form');
    if (!f || !r || !r.hasAttribute('data-bl-line')) return false;
    const p = r.getAttribute('data-bl-p') || '', num = r.getAttribute('data-bl-line');
    f.elements.project.value = p;
    f.elements.num.value = num;
    f.elements.text.value = f.elements.text.defaultValue = r.getAttribute('data-bl-text') || '';
    dl.querySelector('[data-bl-edp]').textContent = p + ' #' + num;
    say(f, '');
    pm7.dialog(dl, { opener: opener || r, focus: f.elements.text });
    f.elements.text.select();
    return true;
  }
  async function saveTask(f) {
    const p = f.elements.project.value, t = f.elements.text, text = t.value.trim();
    const ok = f.querySelector('[data-bl-fok]');
    if (!text) { say(f, 'Write the task.'); t.focus(); return; }
    if (text === t.defaultValue.trim()) { f.closest('dialog').close(); return; }
    say(f, '');
    ok.disabled = true;
    const res = await B.post('/p/' + enc(p) + '/todo/edit', { num: f.elements.num.value, text: text }, 'Save');
    ok.disabled = false;
    if (!res.ok) {
      say(f, res.r && res.r.status === 409 ? res.why + ' (' + p + ' keeps its tasks in TODO.md)' : res.why);
      return;
    }
    pm7.toast('Saved: ' + cut(text, 60));
    f.closest('dialog').close();
    pm7.backlog.refresh('bl-todo');
  }

  /* ---------- events ---------- */
  d.addEventListener('change', (e) => {
    const t = e.target;
    if (!t.matches) return;
    if (t.matches('[data-bl-files]')) { take(t.closest('form'), t.files); t.value = ''; }
    else if (t.matches('[data-bl-manyon]')) mode(t.closest('form'));
  });
  d.addEventListener('input', (e) => {
    const f = e.target.closest && e.target.closest('[data-bl-taskform]');
    if (f && !f.querySelector('[data-bl-tfall]').hidden) cmd(f);
  });
  d.addEventListener('submit', (e) => {
    const f = e.target;
    if (f.matches('[data-bl-addform]')) { e.preventDefault(); addIssue(f); }
    else if (f.matches('[data-bl-taskform]')) { e.preventDefault(); addTask(f); }
    else if (f.matches('[data-bl-teditform]')) { e.preventDefault(); saveTask(f); }
  });
  d.addEventListener('click', (e) => {
    const t = e.target;
    if (!t || !t.closest) return;
    let b = t.closest('[data-bl-add]');
    if (b) { openAdd(b.getAttribute('data-bl-add'), b.closest('.bl-c') || d.querySelector('.bl-c'), b); return; }
    if ((b = t.closest('[data-bl-tcopy]'))) {
      const f = b.closest('form');
      if (cmd(f)) B.copy(f.elements.cmd.value);
      return;
    }
    if ((b = t.closest('[data-bl-pick]'))) {
      const i = b.parentNode.querySelector('[data-bl-files]');
      if (i) i.click();
    }
  });
  d.addEventListener('bl:tedit', (e) => {
    const r = e.target.closest && e.target.closest('.bl-c .p-gr[data-row-id]');
    if (r) task(r, e.target);
  });

  pm7.backlog.add = { open: openAdd, task: task };
})();
