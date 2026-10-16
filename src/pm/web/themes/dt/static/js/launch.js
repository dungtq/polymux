/* dt Launch dialog (PRD.md §6.12, LAYOUT.md §5 "Launch", DESIGN.md §6.11, §8 key N): one dialog from anywhere instead
   of the /p/<project>/launch page. Opened by the top-bar Launch button, any [data-l-open] (data-l-project,
   data-l-todo), the session ⋯ item "Switch agent…" (event c:switch: open({handoff: <agent id>})), key N and the
   search command "Launch an agent" (the shell's key-table row, pointed here).
   It loads GET /launch/form?project=&todo=&handoff= (dt _launch_form.html; handoff = an agent id: that chat as the
   prompt to edit) into dialog#l-dlg [data-l-body] and loads again
   when the project picker changes. The form's choices come from its data-l-data JSON (launchctx.launch_data):
   models per kind (families with variants and efforts), permission modes (help sentence, bypass), the remembered
   launch of the project (prefilled), worktree hints, prompt templates. The prompt box (attachments, @ files) is
   js/launch-prompt.js (pm7.launch.prompt).
   Launch (mod+Enter) posts <base>/launch with open=1 (HX-Request, so the answer is HX-Redirect: /a/<id>) and opens the
   new agent as a tab (pm7.tabs.open); Launch in background posts open=0 and toasts the server's line after it said OK.
   A prompt taken from the From TODO tab and left unchanged (no worktree branch, no attachment) posts <base>/dispatch
   with the item's index instead, so the item is marked in progress like the classic Dispatch.
   Globals: pm7.launch = {open({project, todo, handoff}) -> Promise<bool>, close(), isOpen(), prompt}.
   Layers: pm7.dialog (layers.js: Esc, backdrop, focus trap and return); confirms stack on top of it. Keys: the form's
   own keydown (mod+Enter launches; Enter in a one-line field never submits). A prompt left in the box when the
   dialog closes comes back on the next open of the same project (memory only). */
(function () {
  'use strict';
  const pm7 = window.pm7, d = document;
  const ID = /^[a-z][a-z0-9-]*-[0-9a-f]{4,}$|^[a-z][a-z0-9-]*-[0-9]+$/;
  const DEFAULT_HELP = 'The agent’s own permission mode: it asks before edits and commands.';
  let seq = 0, S = null, draft = null;

  const dlg = () => d.getElementById('l-dlg');
  const $ = (sel) => (S ? S.form.querySelector(sel) : null);
  function isOpen() { const D = dlg(); return !!(D && D.open); }
  const el = pm7.el;  // core.js: the one DOM builder (no HTML strings here)
  function opt(sel, value, text) {
    const o = el('option', null, text);
    o.value = value;
    sel.appendChild(o);
    return o;
  }

  /* ---------- open, load, close ---------- */
  function curProject() {
    const b = d.querySelector('.x-top [data-l-open]');
    return (b && b.getAttribute('data-l-project')) || '';
  }
  function loading(body) {
    body.replaceChildren(el('h2.vh', { id: 'l-dlg-t' }, 'Launch an agent'), el('p.l-wait', null, 'Loading…'));
  }
  async function load(project, todo, handoff) {
    const D = dlg(), body = D.querySelector('[data-l-body]'), n = ++seq;
    const q = new URLSearchParams();
    if (project) q.set('project', project);
    if (todo != null && todo !== '') q.set('todo', String(todo));
    if (handoff) q.set('handoff', String(handoff));
    let r = null;
    try { r = await pm7.fetch('/launch/form?' + q.toString()); } catch (e) { r = null; }
    if (n !== seq || !D.open) return false;
    if (!r || !r.ok) {
      const why = await pm7.why(r, r && r.status === 404 ? 'No project to launch in' : 'Could not load the form');
      body.replaceChildren(el('div.l-hd', null, el('h2', { id: 'l-dlg-t' }, 'Launch an agent'), el('span.sp')),
        el('p.l-err', null, why),
        el('div.l-ft', null, el('span.sp'), el('button.x-btn', { type: 'button', 'data-act': 'close' }, 'Close')));
      return false;
    }
    body.innerHTML = await r.text();  // server-rendered, escaped template
    const form = body.querySelector('[data-l-form]');
    if (!form) return false;
    wire(form);
    if (pm7.keys) pm7.keys.fill(body);
    return true;
  }
  async function open(opts) {
    const o = opts || {}, D = dlg();
    if (!D) {
      window.location.assign('/launch' + (o.project ? '?project=' + encodeURIComponent(o.project) : ''));
      return false;
    }
    if (!D.open) {
      loading(D.querySelector('[data-l-body]'));
      pm7.dialog(D, { opener: d.activeElement, onClose: closed });
    }
    const ok = await load(o.project || curProject(), o.todo, o.handoff);
    if (ok && D.open) {
      const f = pm7.phone() ? D.querySelector('[data-act=close]') : $('[data-l-prompt]');
      if (f) f.focus({ preventScroll: true });
    }
    return ok;
  }
  function closed() {
    seq++;
    const ta = $('[data-l-prompt]');
    if (S && ta && ta.value.trim() && !S.sent) draft = { project: S.project, text: ta.value };
    S = null;
  }
  function close() { const D = dlg(); if (D && D.open) pm7.layers.close(D); }

  /* ---------- wiring one loaded form ---------- */
  function json(form) {
    try { return JSON.parse(form.getAttribute('data-l-data') || '{}') || {}; } catch (e) { return {}; }
  }
  function wire(form) {
    const data = json(form), rem = data.remembered || {};
    S = { form: form, data: data, base: form.getAttribute('data-l-base') || '', sent: false, busy: false,
      project: form.getAttribute('data-l-project') || '', kind: '', model: '', todo: null, tplText: '' };
    const sel = form.getAttribute('data-l-todosel');
    if (sel !== '') {
      const b = form.querySelector('[data-l-todo="' + sel + '"]');
      if (b) S.todo = { index: sel, prompt: b.getAttribute('data-l-tprompt') || '' };
    }
    setKind((form.querySelector('input[name=kind]:checked') || {}).value || '', rem);
    if (rem.branch) setWorktree(true, rem.branch);
    worktreeHints();
    const ta = $('[data-l-prompt]');
    if (ta && !ta.value && draft && draft.project === S.project) ta.value = draft.text;
    draft = null;
    if (pm7.launch.prompt) pm7.launch.prompt.wire(form, S.base);
    form.addEventListener('change', onChange);
    form.addEventListener('input', onInput);
    form.addEventListener('click', onClick);
    form.addEventListener('keydown', onKey);
    form.addEventListener('submit', (e) => {
      e.preventDefault();
      const b = e.submitter;
      if (b && b.hasAttribute('data-l-go')) submit(b.getAttribute('data-l-go'));
    });
  }

  /* ---------- agent kind, model, variant, effort ---------- */
  function fams() { return ((S.data.models || {})[S.kind]) || []; }
  function famOf(name) { return fams().find((f) => f.name === name) || null; }
  function spec() { return ((S.data.modes || {})[S.kind]) || { modes: [], extra: null, efforts: [] }; }
  function setKind(kind, rem) {
    S.kind = kind;
    const r = rem || {};
    const want = r.kind === kind ? r.model_choice || '' : '';
    chooseModel(want === 'custom' || famOf(want) ? want : '', r.kind === kind ? r : null);
    renderModes(r.kind === kind ? r : null);
  }
  function chooseModel(value, rem) {
    S.model = value;
    $('[data-l-mchoice]').value = value;
    const f = famOf(value), n = fams().length;
    $('[data-l-mname]').textContent = value === 'custom' ? 'custom id' : value || 'default';
    $('[data-l-mnote]').textContent = value === 'custom' ? 'type the model id below' : f
      ? (f.variants.length > 1 ? f.variants.length + ' variants' : f.variants[0][1] || '')
      : (n ? 'the agent’s own · ' + n + ' more' : 'the agent’s own');
    const vrow = $('[data-l-varrow]'), vs = $('[data-l-var]');
    vs.textContent = '';
    if (f) f.variants.forEach((v) => opt(vs, v[0], v[0] === v[1] ? v[0] : v[0] + ' — ' + v[1]));
    vrow.hidden = !f || f.variants.length < 2;
    vs.disabled = vrow.hidden;
    if (rem && rem.variant && f && Array.from(vs.options).some((o) => o.value === rem.variant)) vs.value = rem.variant;
    const cust = $('[data-l-custom]'), ci = cust.querySelector('input');
    cust.hidden = value !== 'custom';
    ci.disabled = cust.hidden;
    if (rem && value === 'custom') ci.value = rem.model || '';
    renderEfforts(rem ? rem.effort : null);
    showModels(false);
  }
  function renderEfforts(want) {
    const f = famOf(S.model), eff = f && f.efforts.length ? f.efforts : spec().efforts || [];
    const hidden = $('[data-l-effort]'), cur = want != null ? want : hidden.value;
    const val = eff.indexOf(cur) >= 0 ? cur : '';
    hidden.value = val;
    $('[data-l-effrow]').hidden = !eff.length;
    $('[data-l-eff]').replaceChildren(...[''].concat(eff).map((e) => el('button', { type: 'button', 'data-l-e': e,
      'aria-pressed': String(e === val) }, e || 'default')));
  }
  function showModels(on) {
    const list = $('[data-l-mlist]'), b = $('[data-l-model]');
    list.hidden = !on;
    b.setAttribute('aria-expanded', on ? 'true' : 'false');
    if (!on) return;
    const q = $('[data-l-mq]');
    q.value = '';
    renderModelList();
    q.focus();
  }
  function renderModelList() {
    const q = $('[data-l-mq]').value.trim().toLowerCase(), all = fams();
    const hay = (f) => (f.name + ' ' + f.variants.map((v) => v[0] + ' ' + v[1]).join(' ')).toLowerCase();
    const rows = [['', 'default', 'the agent’s own']]
      .concat(all.filter((f) => !q || hay(f).indexOf(q) >= 0).map((f) => [f.name, f.name,
        f.variants.length > 1 ? f.variants.length + ' variants' : f.variants[0][0]]))
      .concat([['custom', 'custom id…', 'any model id']]);
    $('[data-l-mopts]').replaceChildren(...rows.map((m) => el('button', { type: 'button', role: 'option',
      'data-l-m': m[0], 'aria-selected': String(m[0] === S.model) }, el('b', null, m[1]), el('small', null, m[2]))),
    ...(all.length ? [] : [el('p.l-help', null, 'No model list from this CLI: use default or a custom id.')]));
  }

  /* ---------- permissions ---------- */
  function renderModes(rem) {
    const sp = spec(), keep = rem ? rem.mode || '' : $('[data-l-mode]').value;
    const modes = [{ key: '', help: DEFAULT_HELP, bypass: false }].concat(sp.modes || []);
    S.modes = modes;
    $('[data-l-mlabel]').textContent = sp.mode_label || 'Permissions';
    $('[data-l-modes]').replaceChildren(...modes.map((m) => el('button', { type: 'button', 'data-l-pm': m.key,
      cls: m.bypass ? 'red' : null, title: m.bypass ? 'Bypass: no permission checks' : null },
    (m.key || 'default') + (m.bypass ? ' ⚠' : ''))));
    const xs = $('[data-l-x]');
    xs.textContent = '';
    if (sp.extra) {
      $('[data-l-xlabel]').textContent = sp.extra.label;
      opt(xs, '', 'default');
      sp.extra.options.forEach((o) => opt(xs, o.key, o.key));
      if (rem && rem.extra) xs.value = sp.extra.options.some((o) => o.key === rem.extra) ? rem.extra : '';
    }
    setMode(modes.some((m) => m.key === keep && !m.bypass) ? keep : '');
  }
  function setMode(key) {
    const m = S.modes.find((x) => x.key === key) || S.modes[0], sp = spec();
    $('[data-l-mode]').value = m.key;
    S.form.querySelectorAll('[data-l-pm]').forEach((b) => b.setAttribute('aria-pressed',
      String(b.getAttribute('data-l-pm') === m.key)));
    const xrow = $('[data-l-xrow]'), xs = $('[data-l-x]');
    xrow.hidden = !sp.extra || (S.kind === 'codex' && m.key === 'bypass');
    xs.disabled = xrow.hidden;
    const ok = $('[data-l-ok]');
    $('[data-l-okrow]').hidden = !m.bypass;
    ok.disabled = !m.bypass;
    ok.checked = false;
    help();
  }
  function help() {
    const m = S.modes.find((x) => x.key === $('[data-l-mode]').value) || S.modes[0], sp = spec(), xs = $('[data-l-x]');
    let t = m.key ? m.key + ': ' + (m.help || '') : m.help;
    const xo = !xs.disabled && xs.value && sp.extra ? sp.extra.options.find((o) => o.key === xs.value) : null;
    if (xo) t += ' ' + xo.key + ': ' + (xo.help || '');
    $('[data-l-help]').textContent = t;
    gate();
  }
  function bypassBlocked() {
    const ok = $('[data-l-ok]');
    return !!ok && !ok.disabled && !ok.checked;
  }
  function gate() {
    const b = bypassBlocked() || (S && S.busy);
    S.form.querySelectorAll('[data-l-go]').forEach((x) => {
      x.disabled = b;
      x.title = bypassBlocked() ? 'Tick “I understand” first' : '';
    });
  }

  /* ---------- where it runs: worktree branch ---------- */
  function setWorktree(on, branch) {
    const sw = $('button.sw[data-l-wt]'), row = $('[data-l-brrow]'), bi = $('[data-l-branch]');
    if (sw) sw.setAttribute('aria-checked', on ? 'true' : 'false');
    row.hidden = !on;
    bi.disabled = !on;
    if (branch != null) bi.value = branch;
    worktreeHints();
    if (on && branch == null) bi.focus();
  }
  function worktreeHints() {
    const wt = S.data.worktree || {}, bi = $('[data-l-branch]'), hint = $('[data-l-bhint]'), dl = $('[data-l-blist]');
    if (dl && !dl.childElementCount) (wt.branches || []).forEach((b) => {
      const o = opt(dl, b, b);
      if (wt.taken && wt.taken[b]) o.label = 'checked out: ' + wt.taken[b];
    });
    const b = bi.disabled ? '' : bi.value.trim(), taken = b && wt.taken ? wt.taken[b] : '';
    hint.classList.toggle('warn', !!taken);
    if (bi.disabled || !b) {
      hint.replaceChildren(...(bi.disabled ? hintOff() : ['Type a branch name (new or existing).']));
      return;
    }
    if (taken) {
      hint.textContent = 'Branch ' + b + ' is checked out in ' + taken + '; the launch will be refused.';
      return;
    }
    hint.textContent = ((wt.branches || []).indexOf(b) >= 0 ? 'Existing branch ' : 'New branch ') + b +
      ' in a worktree at ' + (wt.base || '') + '/' + b.replace(/\//g, '-');
  }
  function hintOff() {  // the same sentence as the template's first paint (_launch_form.html data-l-bhint)
    const p = S.form.getAttribute('data-l-path') || '';
    return ['Off: runs in ', p ? el('span.mono', null, p) : 'the project directory', ' with the others.'];
  }

  /* ---------- events ---------- */
  function onChange(e) {
    const t = e.target;
    if (t.name === 'kind') { setKind(t.value, null); return; }
    if (t.matches('[data-l-proj]')) { load(t.value).then((ok) => { if (ok) focusFirst(); }); return; }
    if (t.matches('[data-l-x]')) help();
    else if (t.matches('[data-l-ok]')) gate();
  }
  function onInput(e) {
    const t = e.target;
    if (t.matches('[data-l-mq]')) renderModelList();
    else if (t.matches('[data-l-branch]')) worktreeHints();
    else if (t.matches('[data-l-prompt]') && S.todo) $('[data-l-todonote]').hidden = t.value !== S.todo.prompt;
  }
  function focusFirst() { const f = $('[data-l-proj]'); if (f) f.focus(); }
  function onClick(e) {
    const t = e.target.closest ? e.target : null;
    if (!t) return;
    let b;
    if ((b = t.closest('[data-l-model]'))) { showModels(b.getAttribute('aria-expanded') !== 'true'); return; }
    if ((b = t.closest('[data-l-m]'))) {
      chooseModel(b.getAttribute('data-l-m'), null);
      $('[data-l-model]').focus();
      return;
    }
    if ((b = t.closest('[data-l-e]'))) {
      $('[data-l-effort]').value = b.getAttribute('data-l-e');
      S.form.querySelectorAll('[data-l-e]').forEach((x) => x.setAttribute('aria-pressed', String(x === b)));
      return;
    }
    if ((b = t.closest('[data-l-pm]'))) { setMode(b.getAttribute('data-l-pm')); return; }
    if ((b = t.closest('button.sw[data-l-wt]'))) { setWorktree(b.getAttribute('aria-checked') === 'true'); return; }
    if ((b = t.closest('[data-l-tpl]'))) { template(b); return; }
    if ((b = t.closest('[data-l-todo]'))) { useTodo(b); return; }
    if ((b = t.closest('[data-l-switch]'))) {  // the launch_redirects hint: switch the picker to that project
      const p = b.getAttribute('data-l-switch');
      $('[data-l-proj]').value = p;
      load(p).then((ok) => { if (ok) focusFirst(); });
      return;
    }
    if ((b = t.closest('a[data-l-agent]')) && !e.metaKey && !e.ctrlKey && !e.shiftKey) {
      e.preventDefault();
      openAgent(b.getAttribute('data-l-agent'));
    }
  }
  function onKey(e) {
    if (e.key !== 'Enter' || e.isComposing) return;
    const t = e.target;
    if ((e.metaKey || e.ctrlKey) && !e.altKey) { e.preventDefault(); submit('tab'); return; }
    if (t.matches('[data-l-mq]')) {  // Enter in the model filter takes the first match
      e.preventDefault();
      const f = S.form.querySelector('[data-l-mopts] [data-l-m]:not([data-l-m=""])');
      if (f) f.click();
      return;
    }
    if (t.tagName === 'INPUT' && t.type !== 'checkbox' && t.type !== 'radio') e.preventDefault();
  }
  async function template(b) {
    const t = (S.data.templates || [])[+b.getAttribute('data-l-tpl')], ta = $('[data-l-prompt]');
    if (!t || !ta) return;
    if (ta.value.trim() && ta.value !== S.tplText) {
      const ok = await pm7.confirm({ title: 'Replace the prompt?', ok: 'Replace', opener: b,
        body: 'The text in the prompt box is replaced by the “' + t.name + '” template.' });
      if (!ok || !S) return;
    }
    S.tplText = t.text;
    ta.value = t.text;
    ta.dispatchEvent(new Event('input', { bubbles: true }));
    ta.focus();
  }
  function useTodo(b) {
    const ta = $('[data-l-prompt]');
    S.todo = { index: b.getAttribute('data-l-todo'), prompt: b.getAttribute('data-l-tprompt') || '' };
    ta.value = S.todo.prompt;
    $('[data-l-todonote]').hidden = false;
    const tab = S.form.querySelector('[data-p-tab="l-new"]');
    if (tab) tab.click();
    ta.focus();
    ta.setSelectionRange(0, 0);
    ta.scrollTop = 0;
  }
  function openAgent(id) {
    if (!ID.test(id || '')) return;
    close();
    if (pm7.tabs) pm7.tabs.open('agent', id, '/a/' + id); else window.location.assign('/a/' + id);
  }

  /* ---------- launch ---------- */
  function showErr(text) {
    const p = $('[data-l-err]');
    if (!p) return;
    p.textContent = text || '';
    p.hidden = !text;
  }
  function body(go) {
    const fd = new URLSearchParams(new FormData(S.form));
    fd.set('open', go === 'tab' ? '1' : '0');
    const P = pm7.launch.prompt, paths = P ? P.paths(S.form) : [];
    const typed = (fd.get('prompt') || '').replace(/\s+$/, '');
    if (paths.length) fd.set('prompt', (typed ? typed + '\n' : '') + paths.join('\n'));
    const todo = !!S.todo && !paths.length && !fd.get('branch') && $('[data-l-prompt]').value === S.todo.prompt;
    if (todo) fd.set('index', S.todo.index);
    return { fd: fd, url: S.base + (todo ? '/dispatch' : '/launch'), todo: todo };
  }
  function flashText(html) {
    const p = new DOMParser().parseFromString(html || '', 'text/html').querySelector('p');
    return p ? p.textContent.trim() : '';
  }
  async function submit(go) {
    if (!S || S.busy) return;
    if (bypassBlocked()) {
      showErr('Tick “I understand” to launch without permission prompts.');
      $('[data-l-ok]').focus();
      return;
    }
    const P = pm7.launch.prompt;
    if (P && P.uploading(S.form)) { showErr('Still uploading; launch when the files are attached.'); return; }
    showErr('');
    const st = S, req = body(go);
    st.busy = true;
    gate();
    let r = null;
    try { r = await pm7.fetch(req.url, { form: req.fd, headers: { 'HX-Request': 'true' } }); } catch (e) { r = null; }
    if (S !== st) return;
    st.busy = false;
    gate();
    if (!r || !r.ok) { showErr(await pm7.why(r, req.todo ? 'Dispatch failed' : 'Launch failed')); return; }
    st.sent = true;
    const to = r.headers.get('HX-Redirect') || '', m = /^\/a\/([a-z0-9-]+)(?:\?.*)?$/.exec(to);
    const text = flashText(await r.text()) || 'Launched ' + (req.fd.get('kind') || 'agent') + ' in ' + st.project;
    close();
    if (go === 'tab' && m) { openAgent(m[1]); return; }
    pm7.toast(text);
  }

  /* ---------- openers: [data-l-open] (top-bar Launch), key N, the search command ---------- */
  d.addEventListener('click', (e) => {
    const a = e.target && e.target.closest ? e.target.closest('[data-l-open]') : null;
    if (!a || e.metaKey || e.ctrlKey || e.shiftKey || e.button) return;
    e.preventDefault();
    open({ project: a.getAttribute('data-l-project') || '', todo: a.getAttribute('data-l-todo') });
  });

  /* ⋯ Switch agent… (session/_frame.html menu, dt.session): the agent of the page it was clicked in */
  d.addEventListener('c:switch', (e) => {
    const s = e.target && e.target.closest ? e.target.closest('[data-agent]') : null;
    const id = s ? s.getAttribute('data-agent') : '';
    if (id && ID.test(id)) open({ handoff: id });
  });

  pm7.launch = Object.assign(pm7.launch || {}, { open: open, close: close, isOpen: isOpen });
  pm7.ready(() => {
    const row = pm7.keys && pm7.keys.table.find((t) => t.id === 'launch');
    if (row) row.run = () => { open({}); };
  });
})();
