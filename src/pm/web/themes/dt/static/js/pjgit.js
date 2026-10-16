/* dt theme (pm v7) project view, Git tab (docs/themes/dt/PRD.md §6.4): status line with Fetch / Pull / Push to gitdev,
   guard lines (detached, merge in progress, behind, diverged, clash -> Stash, pull and pop, a stash pop that clashed),
   changes with stage boxes and a diff in each row's details (Stage / Unstage / Discard), the commit bar (message,
   Commit, Stash), the stash list (Pop, ⋯ Drop), branches (Checkout, ⋯ Merge / Push / Delete) and the last commits.
   Data: GET <base>/git/status (JSON, rendered here with DOM APIs; text only through textContent). Every action
   that changes the repo first asks GET <base>/git/plan?action=… what it will do and shows that text in a plain
   in-page question (risk safe / confirm) or a typed one (risk typed: the plan's `confirm` value), then POSTs
   <base>/git/<action> with the plan's `requires` fields; a blocked plan is said in a toast and nothing runs.
   Stage / unstage boxes: the box's label is the plan ("Stage <path>"); they run after the plan check without a
   dialog (reversible in one click). After an action: this tab reloads and the frame's head (pm7.pj.refresh).
   Fetch has no plan in the core: its title says what it does (downloads gitdev's commits; no branch or file changes).
   Push is POST <base>/git/push (gitdev only, never forced; the core has no force path). Diffs: GET
   <base>/files?path=<p>&diff=1 (the core's diff source); only its df-* line spans are read, as text; ⋯ also opens
   that fragment in the frame's reader aside (pm7.pj.reader, PARTS.md §14).
   Hooks: [data-pjops=git][data-pj-base] (inside the frame's tab panel) >
   [data-pjg-body]. Helpers: js/pjops.js (pm7.pjops). */
(function () {
  'use strict';
  const pm7 = window.pm7, d = document;
  if (!pm7 || pm7.pjgit) return;
  const P = () => pm7.pjops;
  const S = { msg: '', clash: null, busy: false };   // commit message kept across renders; last clash; one action
  const TBL = 'pjg-chg';

  const plural = (n, w) => n + ' ' + w + (n === 1 ? '' : 's');
  const base = (sec) => sec.getAttribute('data-pj-base') || '';
  const secOf = () => d.querySelector('[data-pjops="git"]');

  /* ---------- plan -> confirm -> POST ---------- */
  async function plan(sec, action, args) {
    const q = P().params(Object.assign({ action: action }, args || {}));
    let r = null;
    try { r = await pm7.fetch(base(sec) + '/git/plan?' + q.toString()); } catch (e) { r = null; }
    if (!r || !r.ok) { pm7.fail('Git', r); return null; }
    try { return await r.json(); } catch (e) { pm7.toast('Git: bad answer', '', { error: true }); return null; }
  }
  const stashing = (p) => !!(p.requires && p.requires.stash);
  /* o: {action, args, title, ok, url (each also (plan) -> text), form(plan) -> fields, ask (default true), after(res),
     reload (also when nothing ran)} */
  async function run(o) {
    const sec = secOf();
    if (!sec || S.busy) return;
    S.busy = true;
    sec.setAttribute('aria-busy', 'true');
    let ran = false;
    try {
      const p = await plan(sec, o.action, o.args);
      if (!p) return;
      if (p.blocked) { pm7.toast(p.text, '', { error: !!p.guard }); return; }
      const title = typeof o.title === 'function' ? o.title(p) : o.title;
      if (o.ask !== false) {
        const typed = p.risk === 'typed' ? p.confirm : null;
        const ok = typeof o.ok === 'function' ? o.ok(p) : o.ok;
        const yes = await pm7.confirm({ title: title, body: p.text, ok: ok, danger: !!typed, typed: typed });
        if (!yes) return;
      }
      const form = Object.assign({}, p.requires || {}, o.form ? o.form(p) : {});
      if (p.risk === 'typed') form.confirm = p.confirm;
      const url = typeof o.url === 'function' ? o.url(p) : o.url;
      ran = true;
      const res = await P().post(base(sec) + url, form, title.replace(/\?$/, ''));
      const files = res.ok ? res.clash : res.files;
      S.clash = files && files.length ? { text: res.text, files: files } : null;
      if (res.ok && o.after) o.after(res);
    } finally {
      S.busy = false;
      sec.removeAttribute('aria-busy');
      if (ran || o.reload) load(sec, true);   // a box the user ticked shows the real state again
      if (ran) head();
    }
  }
  const pull = () => run({ action: 'pull', title: (p) => (stashing(p) ? 'Stash, pull and pop?' : 'Pull from gitdev?'),
    ok: (p) => (stashing(p) ? 'Stash, pull and pop' : 'Pull'),
    url: (p) => (stashing(p) ? '/git/pull-stash' : '/git/pull') });
  const push = () => run({ action: 'push', title: 'Push to gitdev?', ok: 'Push', url: '/git/push' });
  async function fetchRemote() {
    const sec = secOf();
    if (!sec || S.busy) return;
    S.busy = true;
    try { await P().post(base(sec) + '/git/fetch', {}, 'Fetch'); }
    finally { S.busy = false; load(sec, true); head(); }
  }
  /* the frame's stats line and tiles (PARTS.md §14): its exported refresh when there is one */
  function head() { if (pm7.pj && typeof pm7.pj.refresh === 'function') pm7.pj.refresh(); }
  const stage = (f, on) => run({ action: on ? 'stage' : 'unstage', args: { paths: [f.path] }, ask: false, reload: 1,
    title: (on ? 'Stage ' : 'Unstage ') + f.path, url: on ? '/git/stage' : '/git/unstage',
    form: () => ({ paths: f.path }) });
  const isNew = (p) => !!(p.requires && p.requires.untracked);
  const discard = (f) => run({ action: 'discard', args: { path: f.path }, url: '/git/discard',
    title: (p) => (isNew(p) ? 'Delete ' : 'Discard changes to ') + f.path + '?',
    ok: (p) => (isNew(p) ? 'Delete' : 'Discard'), form: () => ({ path: f.path }) });
  function commit() {
    const msg = S.msg.trim();
    if (!msg) { const i = d.querySelector('[data-pjg-msg]'); if (i) i.focus(); return; }
    const br = S.view && S.view.status.branch;
    run({ action: 'commit', args: { message: msg }, title: 'Commit' + (br ? ' on ' + br : '') + '?', ok: 'Commit',
      url: '/git/commit',
      form: () => ({ message: msg }), after: () => { S.msg = ''; } });
  }
  const stash = () => run({ action: 'stash', title: 'Stash changes?', ok: 'Stash', url: '/git/stash' });
  const pop = (s) => run({ action: 'stash-pop', args: { index: s.index }, title: 'Pop ' + s.ref + '?', ok: 'Pop',
    url: '/git/stash-pop', form: () => ({ index: s.index }) });
  const drop = (s) => run({ action: 'stash-drop', args: { index: s.index }, title: 'Drop ' + s.ref + '?',
    ok: 'Drop', url: '/git/stash-drop', form: () => ({ index: s.index }) });
  const checkout = (b) => run({ action: 'checkout', args: { branch: b }, title: 'Check out ' + b + '?',
    ok: 'Check out', url: '/git/checkout', form: () => ({ branch: b }) });
  const merge = (b, into) => run({ action: 'merge', args: { branch: b }, title: 'Merge ' + b + ' into ' + into + '?',
    ok: 'Merge', url: '/git/merge', form: () => ({ branch: b }) });
  const delBranch = (b) => run({ action: 'branch-delete', args: { branch: b }, title: 'Delete branch ' + b + '?',
    ok: 'Delete', url: '/git/branch-delete', form: () => ({ branch: b }) });

  /* ---------- words ---------- */
  function fileWord(f) {
    if (f.conflict) return 'conflict';
    if (f.untracked) return 'untracked';
    if (f.staged && f.unstaged) return 'partly staged';
    return f.staged ? 'staged' : 'not staged';
  }
  function code(f) {
    if (f.untracked) return '?';
    const c = String(f.xy || '').replace(/\./g, '');
    return c.length === 2 && c[0] === c[1] ? c[0] : c || '·';
  }
  function codeCls(f) {
    if (f.conflict) return 'bad';
    if (f.untracked) return 'u';
    return /A/.test(f.xy) ? 'a' : /D/.test(f.xy) ? 'del' : 'm';
  }
  function ab(a, b, up) {
    const H = P().h, out = [];
    if (a) out.push(H('span.ok', { title: a + ' to push' }, '↑' + a));
    if (b) out.push(H('span.warn', { title: b + ' to pull' }, '↓' + b));
    if (!out.length) out.push(H('span.muted', null, up ? 'in sync' : 'no upstream'));
    return out;
  }

  /* ---------- sections ---------- */
  function tools(v) {
    const H = P().h, st = v.status, c = st.counts, n = st.files.length;
    const br = st.branch || 'detached at ' + (st.head || '?');
    const parts = [n ? plural(st.total || n, 'change') + ' · ' + c.staged + ' staged' : 'clean'];
    const up = st.upstream ? [' · ', ...ab(st.ahead, st.behind, true), H('span.muted', null, ' ' + st.upstream)] : [];
    const off = !v.has_remote, why = off ? 'No ' + v.remote + ' remote' : null;
    return H('div.p-tools.pj-gtools', null,
      H('span.chip.pj-br', { title: 'Current branch' }, P().icon('branch'), H('span', null, br)),
      H('span.note', null, parts, up), H('span.sp'),
      H('span.pj-btns', null,
        P().btn('Fetch', { icon: 'refresh', disabled: off, run: fetchRemote,
          title: why || 'Fetch ' + v.remote + ': download its new commits; no branch or file changes' }),
        P().btn('Pull', { icon: 'down', disabled: off || !st.branch, run: pull,
          title: why || 'Fast-forward ' + br + ' from ' + v.remote + ' (asks first)' }),
        P().btn('Push to ' + v.remote, { icon: 'up', disabled: off || !st.branch, run: push,
          title: why || 'Push ' + br + ' to ' + v.remote + '; never forced (asks first)' })));
  }
  function guards(v, pp) {
    const st = v.status, B = P().ban, btn = P().btn, out = [];
    if (S.clash) {
      out.push(B('bad', S.clash.text + ' Files: ' + S.clash.files.slice(0, 8).join(', ') +
        (S.clash.files.length > 8 ? ' and ' + (S.clash.files.length - 8) + ' more' : '') + '.',
      [btn('Dismiss', { run: () => { S.clash = null; load(secOf(), true); } })]));
    }
    if (st.detached) out.push(B('warn', 'Detached HEAD at ' + (st.head || '?') + ': check out a branch to commit, ' +
      'pull or push.'));
    const cf = st.files.filter((f) => f.conflict).map((f) => f.path);
    if (st.merging || cf.length) {
      out.push(B('bad', (st.merging ? 'A merge is in progress. ' : '') + (cf.length ? plural(cf.length, 'file') +
        ' with conflicts: ' + cf.slice(0, 5).join(', ') + (cf.length > 5 ? ' and ' + (cf.length - 5) + ' more' : '') +
        '. Fix the conflict markers, stage the files, then commit.' : 'Commit to finish it.')));
    }
    const up = st.upstream || v.remote + '/' + st.branch;
    if (st.ahead && st.behind) {
      out.push(B('warn', st.branch + ' and ' + up + ' have diverged (↑' + st.ahead + ' ↓' + st.behind + '). pm never ' +
        'rebases or force-pushes: merge ' + up + ' into ' + st.branch + ' in a terminal or with an agent, then push.'));
    } else if (st.behind && pp && !pp.blocked) {
      const clash = pp.guard === 'clash';
      out.push(B('warn', clash ? pp.text : up + ' has ' + plural(st.behind, 'new commit') + '. ' + pp.text,
        [btn(clash ? 'Stash, pull and pop' : 'Pull', { icon: 'down', run: pull })]));
    } else if (st.behind && pp && pp.blocked) {
      out.push(B('warn', pp.text));
    }
    if (!v.has_remote) {
      out.push(B('note', 'No ' + v.remote + ' remote: Fetch, Pull and Push are off. pm pushes only to ' + v.remote +
        ', never forced.'));
    }
    return out;
  }

  function stageBox(f) {
    const H = P().h, full = f.staged && !f.unstaged && !f.untracked;
    const lab = (full ? 'Unstage ' : 'Stage ') + f.path;
    const box = H('input', { type: 'checkbox', 'aria-label': lab, title: lab, checked: full,
      on: { change: (e) => { e.preventDefault(); stage(f, !full); } } });
    box.indeterminate = f.staged && f.unstaged;
    return H('span.gc-sw', { role: 'cell' }, H('label.pj-ck', null, box));
  }
  function fileMenu(f, full) {
    return [
      { label: full ? 'Unstage' : 'Stage', icon: full ? 'x' : 'check', run: () => stage(f, !full) },
      { label: 'Show diff', icon: 'file', kbd: 'X', run: () => { const r = rowOf(f.path); if (r) pm7.table.det(r); } },
      pm7.pj && pm7.pj.reader ? { label: 'Open diff in the reader', icon: 'side-r', run: () => reader(f) } : null,
      { sep: true },
      { label: f.untracked ? 'Delete new file…' : 'Discard changes…', icon: 'trash', danger: true,
        run: () => discard(f) }].filter(Boolean);
  }
  function reader(f) {
    const sec = secOf(), q = new URLSearchParams({ path: f.path, diff: '1' });
    if (sec) pm7.pj.reader.open({ title: 'Diff of ' + f.path, url: base(sec) + '/files?' + q.toString() });
  }
  function rowOf(id) {
    const t = d.querySelector('[data-p-table="' + TBL + '"]');
    return t ? t.querySelector('.p-gr[data-row-id="' + CSS.escape(id) + '"]') : null;
  }
  function changes(v) {
    const X = P(), st = v.status, files = st.files, H = X.h;
    const out = [X.h2('Changes', st.total || files.length)];
    if (!files.length) {
      out.push(X.table('pjg-clean', 'Changes', 'pj-chg', [X.empty('Working tree clean' +
        (v.stashes.length ? '.' : '. Nothing stashed.'))]));
    } else {
      const all = files.every((f) => f.staged && !f.unstaged && !f.untracked);
      const some = (all ? files.filter((f) => f.staged || f.conflict) : files).map((f) => f.path).slice(0, 500);
      const what = all ? 'Unstage all' : 'Stage all';
      const head = H('input', { type: 'checkbox', checked: all, 'aria-label': what, title: what,
        on: { change: () => run({ action: all ? 'unstage' : 'stage', args: { paths: some }, title: what + '?',
          ok: all ? 'Unstage' : 'Stage', url: all ? '/git/unstage' : '/git/stage',
          form: () => ({ paths: some }) }) } });
      head.indeterminate = !all && files.some((f) => f.staged);
      const rows = [X.gh([{ node: H('label.pj-ck', null, head), cls: 'gc-sw' }, { sr: 'Status' }, { label: 'File' },
        { label: 'State', cls: 'hide-n' }, { sr: 'Actions' }])];
      files.forEach((f) => {
        const full = f.staged && !f.unstaged && !f.untracked, w = fileWord(f);
        const nm = f.orig ? f.orig + ' → ' + f.path : f.path;
        rows.push(X.row(f.path, [stageBox(f),
          X.cell(code(f), 'mono pj-xy ' + codeCls(f)),
          X.name(nm, { sfx: true, title: nm }),
          X.cell(w, 'hide-n ' + (f.conflict ? 'bad' : 'muted')),
          X.meta(code(f) + ' · ' + w),
          X.acts([X.detBtn(TBL, f.path, 'Diff of ' + f.path, { title: 'Diff' }),
            X.menuBtn('Actions for ' + f.path, () => fileMenu(f, full))])], { title: f.path + ': ' + w }));
        rows.push(X.det(TBL, f.path, 'Diff of ' + f.path));
      });
      out.push(X.table(TBL, 'Changes', 'pj-chg', rows, { list: 'pjg-changes', keys: { enter: 'det' },
        labels: { enter: 'Show the diff' } }));
      if (st.total > files.length) out.push(H('p.p-foot', null, 'Showing ' + files.length + ' of ' + st.total));
    }
    out.push(commitBar(v));
    return out;
  }
  function commitBar(v) {
    const H = P().h, st = v.status, staged = st.counts.staged > 0 || st.merging;
    const dirty = st.files.some((f) => !f.untracked);
    const ok = H('button.x-btn.sm', { type: 'button', disabled: !staged || !S.msg.trim(), 'data-pjg-commit': true,
      title: staged ? 'Commit the staged files (asks first)' : 'Stage a file first', on: { click: commit } },
    P().icon('check'), H('span.lb', null, 'Commit'));
    const inp = H('input.x-in', { type: 'text', 'data-pjg-msg': true, placeholder: staged ? 'Commit message'
      : 'Commit message (stage a file first)', 'aria-label': 'Commit message', autocomplete: 'off', value: S.msg,
    on: { input: (e) => { S.msg = e.target.value; ok.disabled = !staged || !S.msg.trim(); },
      keydown: (e) => {
        if (e.key === 'Enter' && !e.isComposing && !ok.disabled) { e.preventDefault(); commit(); }
      } } });
    return H('div.pj-commit', null, inp, ok, P().btn('Stash', { icon: 'box', disabled: !dirty, run: stash,
      title: dirty ? 'Stash the tracked changes (asks first)' : 'No tracked changes to stash' }));
  }
  function stashes(v) {
    const X = P();
    if (!v.stashes.length) return [];
    const rows = [X.gh([{ label: 'Stash' }, { label: 'Message' }, { label: 'Age', num: true }, { sr: 'Actions' }])];
    v.stashes.forEach((s) => rows.push(X.row(s.ref, [X.cell(s.ref, 'mono muted'), X.name(s.subject),
      X.end(s.ts), X.meta(s.ref), X.acts([X.btn('Pop', { run: () => pop(s), title: 'Apply ' + s.ref + ' and drop it' }),
        X.menuBtn('Actions for ' + s.ref, [{ label: 'Pop', icon: 'box', run: () => pop(s) }, { sep: true },
          { label: 'Drop…', icon: 'trash', danger: true, run: () => drop(s) }])])])));
    return [X.h2('Stash', v.stashes.length), X.table('pjg-stash', 'Stash', 'pj-st', rows)];
  }
  function branchMenu(b, st) {
    const cur = st.branch, here = b.name === cur;
    return [
      here ? { label: 'Check out', icon: 'branch', disabled: true, note: 'current branch' }
        : { label: 'Check out', icon: 'branch', run: () => checkout(b.name) },
      here || !cur ? { label: 'Merge into ' + (cur || 'a branch'), icon: 'branch', disabled: true,
        note: here ? 'current branch' : 'detached HEAD' }
        : { label: 'Merge into ' + cur, icon: 'branch', run: () => merge(b.name, cur) },
      here ? { label: 'Push to gitdev', icon: 'up', run: push }
        : { label: 'Push to gitdev', icon: 'up', disabled: true, note: 'check it out first' },
      { sep: true },
      here ? { label: 'Delete branch…', icon: 'trash', disabled: true, note: 'current branch' }
        : { label: 'Delete branch…', icon: 'trash', danger: true, run: () => delBranch(b.name) }];
  }
  function branches(v) {
    const X = P(), H = X.h, st = v.status;
    const rows = [X.gh([{ label: 'Branch' }, { label: 'Upstream', cls: 'hide-n' }, { label: '↑ ↓', cls: 'hide-s',
      title: 'Ahead / behind its upstream' }, { label: 'Last commit', cls: 'hide-n' }, { label: 'Age', num: true },
    { sr: 'Actions' }])];
    v.branches.forEach((b) => {
      const chips = [b.current ? H('span.chip', null, 'current') : null,
        b.gone ? H('span.chip.warn', { title: 'Its upstream branch was deleted' }, 'gone') : null];
      rows.push(X.row(b.name, [X.name(b.name, { chips: chips }),
        X.cell(b.upstream || 'no upstream', 'mono muted hide-n'),
        X.cell(b.upstream ? ab(b.ahead, b.behind, true) : '–', 'hide-s pj-ab'),
        X.cell(b.subject, 'muted hide-n'), X.end(b.ts),
        X.meta(b.upstream ? [b.upstream, ' · ', ...ab(b.ahead, b.behind, true), ' · ', b.subject]
          : ['no upstream · ', b.subject]),
        X.acts([b.current ? null : X.btn('Checkout', { run: () => checkout(b.name), title: 'Check out ' + b.name }),
          X.menuBtn('Actions for ' + b.name, () => branchMenu(b, st))])],
      { title: b.name + (b.current ? ' (current)' : '') }));
    });
    if (v.branches.length === 0) rows.push(X.empty('No branches yet: make the first commit.'));
    return [X.h2('Branches', v.branches.length), X.table('pjg-br', 'Branches', 'pj-brs', rows)];
  }
  function commits(v) {
    const X = P(), st = v.status;
    const rows = [X.gh([{ label: 'Commit' }, { label: 'Message' }, { label: 'Author', cls: 'hide-n' },
      { label: 'Age', num: true }])];
    v.commits.forEach((c) => rows.push(X.row(c.sha, [X.cell(c.hash, 'mono muted'), X.name(c.subject),
      X.cell(c.author, 'muted hide-n'), X.end(c.ts), X.meta(c.hash + ' · ' + c.author)], { title: c.subject })));
    if (!v.commits.length) rows.push(X.empty('No commits yet.'));
    return [X.h2('Commits', null, (st.branch || 'HEAD') + ' · push goes to ' + v.remote + ' only, never forced'),
      X.table('pjg-log', 'Commits', 'pj-cm', rows)];
  }

  /* ---------- diff in a change row's details ---------- */
  const DF = ['df-add', 'df-del', 'df-hunk', 'df-meta', 'df-ctx'];
  async function diff(row) {
    const sec = secOf(), id = row.getAttribute('data-row-id'), det = row.nextElementSibling;
    const box = det && det.matches('.p-det') ? det.querySelector('.p-detc') : null;
    if (!sec || !box || box.hasAttribute('data-pjg-diff')) return;
    box.setAttribute('data-pjg-diff', '');
    const H = P().h, v = S.view, f = v && v.status.files.find((x) => x.path === id);
    box.replaceChildren(H('p.muted', null, 'Loading the diff…'));
    let r = null;
    const q = new URLSearchParams({ path: id, diff: '1' });
    try { r = await pm7.fetch(base(sec) + '/files?' + q.toString()); } catch (e) { r = null; }
    const doc = r && r.ok ? new DOMParser().parseFromString(await r.text(), 'text/html') : null;
    const lines = doc ? Array.from(doc.querySelectorAll('span[class^="df-"]')) : [];
    let view;
    if (lines.length) {
      view = H('pre.pj-diff', { tabindex: '0', 'aria-label': 'Diff of ' + id }, lines.map((s) => {
        const c = DF.find((k) => s.classList.contains(k)) || 'df-ctx';
        return H('span', { cls: c }, (s.textContent || '').replace(/\n$/, ''));
      }));
    } else {
      const why = !r || !r.ok ? 'No diff: ' + (r ? 'the file could not be read (' + r.status + ')' : 'no answer')
        : f && f.untracked ? 'New file, not tracked yet: there is no diff until it is staged and committed.'
          : 'No text diff for this file (binary, deleted or renamed only).';
      view = H('p.muted', null, why);
    }
    const full = f && f.staged && !f.unstaged && !f.untracked;
    const acts = f ? [P().btn(full ? 'Unstage' : 'Stage', { icon: full ? 'x' : 'check', run: () => stage(f, !full) }),
      H('span.sp'), P().btn(f.untracked ? 'Delete new file…' : 'Discard…', { icon: 'trash', cls: 'danger',
        run: () => discard(f) })] : [];
    box.replaceChildren(view, H('div.p-dacts', null, acts));
  }
  d.addEventListener('pm7:det', (e) => {
    const row = e.target;
    if (e.detail && e.detail.open && row.closest && row.closest('[data-p-table="' + TBL + '"]')) diff(row);
  });

  /* ---------- load and render ---------- */
  async function load(sec, again) {
    if (!sec) return;
    const body = sec.querySelector('[data-pjg-body]');
    if (!body) return;
    if (!again && !body.childElementCount) body.replaceChildren(P().h('p.muted.pj-load', null, 'Loading git status…'));
    let r = null;
    try { r = await pm7.fetch(base(sec) + '/git/status'); } catch (e) { r = null; }
    if (!r || !r.ok) {
      const t = await pm7.why(r, 'Git status');
      body.replaceChildren(P().table('pjg-none', 'Git', 'pj-chg', [P().empty(/not a git repo/i.test(t)
        ? 'This project is not a git repository.' : t)]));
      return;
    }
    let v = null;
    try { v = await r.json(); } catch (e) { v = null; }
    if (!v || !v.status) return;
    const st = v.status;
    const pp = st.behind > 0 && !st.ahead && v.has_remote && st.branch ? await plan(sec, 'pull', {}) : null;
    render(sec, body, v, pp);
  }
  function render(sec, body, v, pp) {
    const a = d.activeElement, inside = a && body.contains(a);
    const fRow = inside && a.closest('[data-row-id]') ? a.closest('[data-row-id]').getAttribute('data-row-id') : null;
    const fMsg = inside && a.hasAttribute('data-pjg-msg');
    S.view = v;
    body.replaceChildren(tools(v), ...guards(v, pp), ...changes(v), ...stashes(v), ...branches(v), ...commits(v));
    if (pm7.table) pm7.table.restore(body);
    if (pm7.keys && pm7.keys.fill) pm7.keys.fill(body);
    const inp = fMsg ? body.querySelector('[data-pjg-msg]') : null;
    const fr = !inp && fRow ? body.querySelector('[data-row-id="' + CSS.escape(fRow) + '"]') : null;
    if (inp) { inp.focus(); inp.setSelectionRange(S.msg.length, S.msg.length); } else if (fr) fr.focus();
  }

  pm7.pjgit = { load: () => load(secOf(), true) };
  pm7.ready(() => { if (pm7.pjops) pm7.pjops.tab('git', load); });
})();
