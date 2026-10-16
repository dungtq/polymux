/* dt Backlog: the issue reader, dialog#bl-rd (PRD.md §6.5; PARTS.md §15). Markup: backlog/_dlg_issue.html
   (the dialog) and backlog/_issue_reader.html (its body, the core's GET /p/{n}/issues/{num} through the dt
   _issue_detail.html). Loaded after js/backlog.js wherever the reader dialog is (backlog/_page.html,
   project/_tab_issues.html) and uses its pm7.backlog.lib (one post helper, the lists' refresh, Dispatch, copy).
   Every write is a core tracker route (POST with CSRF, pm7.fetch); a result toast comes only after the server said
   OK (V7-CODE-5), a refusal stays in the reader (the form's or the reader's alert line) and in an error toast.
   The dialog's fixed head (#N, the project: set here on open; Close: focused on open, and again when a reload took
   the focused control away) never scrolls or reloads, so a loading or refused reader can still be closed:
   - [data-bl-rda] done / reopen (POST .../done | .../reopen), dispatch (backlog.js confirm + POST), copy (pmctl),
     edit / cancel (show / hide the Edit form in the body's place), delete (typed confirm "#N", POST .../delete,
     then the reader closes and the lists refresh);
   - form[data-bl-editform]: POST .../edit {title, body} (the stored title and raw Markdown are the form's values);
   - form[data-bl-noteform]: POST .../note {body}.
   After a write the reader body reloads (GET again) and the lists refresh.
   Globals: sets pm7.backlog.reader(project, num, opener). Runs once however many partials load it. */
(function () {
  'use strict';
  const pm7 = window.pm7, d = document;
  if (!pm7 || !pm7.backlog || !pm7.backlog.lib || pm7.backlog.reader) return;
  const B = pm7.backlog.lib, el = pm7.el;
  let rd = null;  // the issue in the reader: {p, n}

  const dlg = () => d.getElementById('bl-rd');
  const of = (art) => ({ p: art.getAttribute('data-bl-p'), n: art.getAttribute('data-bl-n') });
  function say(box, text) {  // an alert line ([data-bl-ferr] / [data-bl-rderr]); '' hides it
    if (!box) return;
    box.textContent = text;
    box.hidden = !text;
  }

  /* ---------- open, (re)load ----------
     The frame's head (#N, the project, Close) stays while the body loads, fails or reloads: Close is always there. */
  const closeBtn = (dl) => dl.querySelector('.bl-rdf [data-act=close]');
  function line(cls, text) {  // the body's one line while loading or after a refusal (the dialog's description)
    const n = el('p', cls, text);
    n.id = 'bl-rd-m';
    if (cls === 'bl-alert') n.setAttribute('role', 'alert');
    return n;
  }
  async function load() {
    const dl = dlg(), box = dl && dl.querySelector('[data-bl-rdbody]');
    if (!box || !rd) return;
    let r = null;
    try { r = await pm7.fetch(B.issueUrl(rd)); } catch (e) { r = null; }
    if (r && r.ok) box.innerHTML = await r.text();  // the server's own fragment (_issue_reader.html), no script
    else box.replaceChildren(line('bl-alert', await pm7.why(r, 'Could not load the issue')));
    const a = d.activeElement;  // focus was in the replaced body (or nowhere): back to Close
    if (dl.open && (!a || a === d.body || !dl.contains(a))) {
      const x = closeBtn(dl);
      if (x) x.focus({ preventScroll: true });
    }
  }
  function reader(p, n, opener) {
    const dl = dlg();
    if (!dl) return;
    rd = { p: p, n: n };
    const no = dl.querySelector('[data-bl-rdno]'), pj = dl.querySelector('[data-bl-rdp]');
    if (no) no.textContent = '#' + n;
    if (pj) { pj.textContent = p || ''; pj.title = p || ''; pj.parentNode.hidden = !p; }
    dl.querySelector('[data-bl-rdbody]').replaceChildren(line('muted', 'Loading…'));
    pm7.dialog(dl, { opener: opener || d.activeElement, focus: closeBtn(dl) });
    load();
  }
  function after() { load(); B.refreshAll(); }

  /* ---------- Edit: the form takes the body's place ---------- */
  function editing(art, on) {
    const f = art.querySelector('[data-bl-editform]'), view = art.querySelector('[data-bl-rdbodyv]');
    const b = art.querySelector('[data-bl-rda=edit]');
    if (!f) return;
    if (!on) f.reset();
    say(f.querySelector('[data-bl-ferr]'), '');
    f.hidden = !on;
    if (view) view.hidden = on;
    if (b) b.setAttribute('aria-expanded', on ? 'true' : 'false');
    const t = on ? f.elements.title : b;
    if (t) t.focus({ preventScroll: !on });
  }
  async function save(f) {
    const art = f.closest('[data-bl-rd]'), err = f.querySelector('[data-bl-ferr]');
    if (!art) return;
    const i = of(art), title = f.elements.title.value.trim();
    if (!title) { say(err, 'The title is required.'); f.elements.title.focus(); return; }
    say(err, '');
    const btn = f.querySelector('[type=submit]');
    btn.disabled = true;
    const res = await B.post(B.issueUrl(i) + '/edit', { title: title, body: f.elements.body.value }, 'Save');
    btn.disabled = false;
    if (!res.ok) { say(err, res.why); return; }
    pm7.toast('#' + i.n + ' saved');
    after();
  }

  /* ---------- Delete: typed "#N" (V7-SEC-6: the issue, its notes and images go for good) ---------- */
  async function del(art, b) {
    const i = of(art), err = art.querySelector('[data-bl-rderr]');
    const ok = await pm7.confirm({ title: 'Delete issue #' + i.n + ' of ' + i.p + '?', typed: '#' + i.n,
      body: 'The issue, its notes and its images are deleted.', ok: 'Delete issue', opener: b });
    if (!ok) return;
    say(err, '');
    b.disabled = true;
    const res = await B.post(B.issueUrl(i) + '/delete', {}, 'Delete #' + i.n);
    b.disabled = false;
    if (!res.ok) { say(err, res.why); pm7.toast(res.why, '', { error: true }); return; }
    pm7.toast('Deleted #' + i.n + ' from ' + i.p);
    rd = null;
    const dl = dlg();
    if (dl && dl.open) pm7.layers.close(dl);
    B.refreshAll();
  }

  /* ---------- the actions row ---------- */
  async function act(b) {
    const a = b.getAttribute('data-bl-rda'), art = b.closest('[data-bl-rd]');
    if (!art) return;
    const i = of(art);
    if (a === 'copy') { B.copy(b.getAttribute('data-bl-copy') || ''); return; }
    if (a === 'edit') { editing(art, b.getAttribute('aria-expanded') !== 'true'); return; }
    if (a === 'cancel') { editing(art, false); return; }
    if (a === 'delete') { del(art, b); return; }
    if (a === 'dispatch') { if (await B.dispatch(i, b)) load(); return; }
    b.disabled = true;
    const res = await B.post(B.issueUrl(i) + '/' + a, {}, a === 'done' ? 'Mark done' : 'Reopen');
    b.disabled = false;
    if (res.ok) pm7.toast('#' + i.n + (a === 'done' ? ' done' : ' reopened'));
    else pm7.toast(res.why, '', { error: true });
    after();
  }
  async function addNote(f) {
    const art = f.closest('[data-bl-rd]'), ta = f.elements.body, err = f.querySelector('[data-bl-ferr]');
    if (!art) return;
    const body = ta.value.trim();
    if (!body) { say(err, 'Write the note first.'); ta.focus(); return; }
    const btn = f.querySelector('[type=submit]');
    btn.disabled = true;
    const i = of(art);
    const res = await B.post(B.issueUrl(i) + '/note', { body: body }, 'Add note');
    btn.disabled = false;
    if (!res.ok) { say(err, res.why); return; }
    pm7.toast('Note added to #' + i.n);
    after();
  }

  /* ---------- events ---------- */
  d.addEventListener('submit', (e) => {
    const f = e.target;
    if (f.matches('[data-bl-noteform]')) { e.preventDefault(); addNote(f); }
    else if (f.matches('[data-bl-editform]')) { e.preventDefault(); save(f); }
  });
  d.addEventListener('click', (e) => {
    const b = e.target && e.target.closest ? e.target.closest('[data-bl-rda]') : null;
    if (b) act(b);
  });

  pm7.backlog.reader = reader;
})();
