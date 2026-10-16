/* dt New project dialog (PRD.md §6.4 last bullet). Markup: templates/projects/_new.html (dialog#pj-new, form
   [data-pj-nf]); opened by js/projects.js through pm7.pjnew.open(opener). Layers (Esc, backdrop, focus trap, focus
   return) are pm7.dialog's; requests go through pm7.fetch (CSRF on the POST).
   Global: pm7.pjnew = {open(opener)}.
   - Plan: every field change (debounced) asks GET /p/new/plan?<fields> -> {ok, error, steps: [{id, text, kind}]} and
     shows the steps ("What will happen") and the error; Create is enabled only when the core says ok. A newer request
     aborts the older one.
   - Run: Create posts the same fields to POST /p/new/run and reads the NDJSON answer line by line: {id, ok, text,
     state: done | skipped | failed | not_run} per step, then {end, ok, project, url, done, failed, not_run}. Each line
     updates its step at once; the summary says what was done, what failed and what did not run (nothing is undone),
     with a link to the project once its folder exists. A refusal before the first step (HTTP 4xx) shows the server's
     message and returns to the form. The toast "Created <name>" comes only after the end line said ok.
     Closing the dialog during a run does not stop it (the stream is still read here); reopening shows its progress.
   - Checkboxes are sent as 1 / 0 (the core defaults readme, git and remote to on when a field is missing).
   - First agent: kind -> model list (launch_data of the Launch form, GET /launch/form, read once: dt [data-l-data] or
     classic #launch-data[data-json]; else "Default" only), effort / permission mode / extra from data-pj-modes
     (launch.form_spec); a bypass mode shows "I understand" (confirm_bypass). */
(function () {
  'use strict';
  const pm7 = window.pm7, d = document;
  const PLAN_MS = 250;
  const WORD = { done: 'done', skipped: 'skipped', failed: 'failed', not_run: 'not run', now: 'running',
    wait: 'waiting' };
  let ctl = null, running = false, ended = false, models = null, loading = null;

  const dlg = () => d.getElementById('pj-new');
  const form = () => { const x = dlg(); return x ? x.querySelector('[data-pj-nf]') : null; };
  const q = (sel) => { const f = form(); return f ? f.querySelector(sel) : null; };
  function json(s, dflt) { try { return JSON.parse(s || ''); } catch (err) { return dflt; } }
  function pathCode(p) {  // a path as code that wraps only after a "/", never at a hyphen (css: .pj-path>span)
    const kids = [];
    (p.match(/[^/]*\/|[^/]+$/g) || [p]).forEach((x, i) => {
      if (i) kids.push(pm7.el('wbr'));
      kids.push(pm7.el('span', null, x));
    });
    return pm7.el('code.pj-path', null, kids);
  }
  function stepText(text) {  // the core's step line; its paths (a word starting with / or ~/) as pathCode
    return String(text || '').split(/(\s+)/).map((w) => (/^~?\/./.test(w) ? pathCode(w) : w));
  }
  function opt(value, text) { const o = d.createElement('option'); o.value = value; o.textContent = text; return o; }
  function fill(sel, items) {  // items: [[value, text]]; keeps the chosen value when it is still offered
    const keep = sel.value;
    sel.replaceChildren(opt('', 'Default'), ...items.map((x) => opt(x[0], x[1])));
    if (items.some((x) => x[0] === keep)) sel.value = keep;
  }

  /* ---------- fields ---------- */
  function fields(f) {
    const e = f.elements, p = new URLSearchParams();
    p.set('name', e.name.value.trim());
    p.set('template', e.template.value);
    ['readme', 'git', 'remote', 'commit'].forEach((k) => p.set(k, e[k].checked && !e[k].disabled ? '1' : '0'));
    const kind = e.agent.value;
    if (kind) {
      p.set('agent', kind);
      ['model', 'effort', 'permission_mode', 'permission_extra'].forEach((k) => {
        if (e[k].value) p.set(k, e[k].value);
      });
      if (e.prompt.value.trim()) p.set('prompt', e.prompt.value);
      if (e.confirm_bypass.checked && !q('[data-pj-bypass]').hidden) p.set('confirm_bypass', '1');
    }
    return p;
  }
  function sync() {  // what depends on other fields: folder preview, template note, git boxes, agent fields
    const f = form(), e = f.elements;
    q('[data-pj-dir]').textContent = (f.getAttribute('data-pj-root') || '') + '/' + (e.name.value.trim() || '…');
    const o = e.template.selectedOptions[0];
    if (o && o.getAttribute('data-desc')) q('[data-pj-tpl]').textContent = o.getAttribute('data-desc');
    const git = e.git.checked;
    pm7.$$('[data-pj-gitdep]', f).forEach((l) => {  // data-pj-never: no git_remote_template, stays off
      const off = !git || l.hasAttribute('data-pj-never');
      l.classList.toggle('off', off);
      l.querySelector('input').disabled = off;
    });
    agentFields(f);
  }

  /* ---------- first agent ---------- */
  function launchData(html) {
    const doc = new DOMParser().parseFromString(html, 'text/html');
    const el = doc.querySelector('[data-l-data]'), cl = doc.querySelector('#launch-data[data-json]');
    return json(el ? el.getAttribute('data-l-data') : cl ? cl.getAttribute('data-json') : '', {});
  }
  function loadModels() {
    if (models || loading) return loading;
    loading = pm7.fetch('/launch/form').then((r) => (r.ok ? r.text() : ''))
      .then((t) => { models = (t && launchData(t).models) || {}; }, () => { models = {}; })
      .then(() => { loading = null; const f = form(); if (f) agentFields(f); });
    return loading;
  }
  function agentFields(f) {
    const e = f.elements, kind = e.agent.value, box = q('[data-pj-agent]');
    box.hidden = !kind;
    if (!kind) { q('[data-pj-bypass]').hidden = true; return; }
    if (models === null) loadModels();
    const fams = (models && models[kind]) || [];
    fill(e.model, fams.flatMap((fm) => (fm.variants || []).map((v) => [String(v[0]), String(v[1] || v[0])])));
    const spec = (json(f.getAttribute('data-pj-modes'), {}) || {})[kind] || {};
    fill(e.effort, (spec.efforts || []).map((x) => [x, x]));
    q('[data-pj-mlabel]').textContent = spec.mode_label || 'Permission mode';
    const modes = spec.modes || [];
    fill(e.permission_mode, modes.map((m) => [m.key, m.key + (m.bypass ? ' (bypass)' : '')]));
    const ex = spec.extra, exw = q('[data-pj-extraw]');
    exw.hidden = !ex;
    if (ex) {
      q('[data-pj-xlabel]').textContent = ex.label || 'Extra';
      fill(e.permission_extra, (ex.options || []).map((x) => [x.key, x.key]));
    }
    const m = modes.find((x) => x.key === e.permission_mode.value);
    q('[data-pj-mhelp]').textContent = m ? m.help || '' : '';
    q('[data-pj-bypass]').hidden = !(m && m.bypass);
  }

  /* ---------- plan ---------- */
  function setErr(text) { const p = q('[data-pj-err]'); p.textContent = text || ''; p.hidden = !text; }
  function renderPlan(j, named) {
    const ol = q('[data-pj-plan]');
    ol.replaceChildren(...(j.steps || []).map((s) => {
      const n = pm7.el('li', s.kind === 'skip' ? 'skip' : null, stepText(s.text));
      n.setAttribute('data-step', s.id);
      return n;
    }));
    const err = named && j.error ? String(j.error) : '';
    setErr(err && err[0].toUpperCase() + err.slice(1));
    q('[data-pj-note]').textContent = named ? 'Nothing is created until you press Create.' : 'Type a name to start.';
    q('[data-pj-create]').disabled = !j.ok;
  }
  async function plan() {
    const f = form();
    if (!f || running || ended) return;
    if (ctl) ctl.abort();
    const my = ctl = new AbortController();
    const p = fields(f);
    let r = null, j = null;
    try {
      r = await pm7.fetch('/p/new/plan?' + p.toString(), { signal: my.signal });
      j = r.ok ? await r.json() : null;
    } catch (err) {
      if (my.signal.aborted) return;
    }
    if (my !== ctl) return;
    if (!j) { setErr(await pm7.why(r, 'The plan is not available')); q('[data-pj-create]').disabled = true; return; }
    renderPlan(j, !!p.get('name'));
  }
  const planSoon = pm7.debounce(plan, PLAN_MS);

  /* ---------- run ---------- */
  function mode(run) {
    q('.pj-ncols').hidden = run;
    q('[data-pj-run]').hidden = !run;
  }
  function mark(n, state, text) {
    n.className = 'st-' + state;
    n.replaceChildren();
    const ic = pm7.icon({ done: 'check', failed: 'x' }[state] || '', 'i');
    n.append(ic || d.createElement('i'));
    const t = d.createElement('span');
    t.textContent = text;
    const w = d.createElement('small');
    w.textContent = WORD[state] || '';
    n.append(t, w);
  }
  function nextNow(ol) {
    const n = ol.querySelector('li.st-wait');
    if (n) mark(n, 'now', n.querySelector('span').textContent);
  }
  function step(ol, s) {
    let n = ol.querySelector('li[data-step="' + CSS.escape(String(s.id)) + '"]');
    if (!n) { n = d.createElement('li'); n.setAttribute('data-step', s.id); ol.appendChild(n); }
    mark(n, WORD[s.state] ? s.state : (s.ok ? 'done' : 'failed'), s.text || s.id);
    if (s.state === 'done' || s.state === 'skipped') nextNow(ol);
  }
  function summary(end, last, name) {
    const p = q('[data-pj-sum]'), open = q('[data-pj-open]');
    p.replaceChildren();
    p.hidden = false;
    let ok = false, url = '';
    if (end && end.ok) {
      ok = true;
      url = end.url;
      p.append('Created ', Object.assign(d.createElement('b'), { textContent: end.project }), '.');
    } else if (end) {
      const n = (st) => pm7.$$('[data-pj-steps] li.st-' + st, form()).length;
      p.append('Stopped at the failed step: ' + n('done') + ' done, ' + n('skipped') + ' skipped, ' +
        n('not_run') + ' not run. Nothing was undone.');
      if ((end.done || []).indexOf('folder') >= 0) url = end.url;
    } else {
      p.append(last && !last.ok ? 'Stopped: ' + (last.text || 'failed') + '.' :
        'The answer ended before the run finished; some steps may have run. Check the Projects list.');
    }
    p.className = 'pj-nsum ' + (ok ? 'ok' : 'bad');
    open.hidden = !url;
    if (url) open.href = url;
    q('[data-pj-create]').hidden = true;
    q('[data-pj-cancel]').textContent = 'Close';
    q('[data-pj-ftnote]').textContent = '';
    if (ok) pm7.toast('Created ' + name);
    const list = d.getElementById('pj');
    if (list && window.htmx) window.htmx.trigger(list, 'pm7:refresh');
  }
  async function run(e) {
    e.preventDefault();
    const f = form(), btn = q('[data-pj-create]');
    if (!f || running || ended || btn.disabled) return;
    const p = fields(f), ol = q('[data-pj-steps]');
    if (ctl) ctl.abort();
    ol.replaceChildren(...pm7.$$('[data-pj-plan] li', f).map((x) => {
      const n = d.createElement('li');
      n.setAttribute('data-step', x.getAttribute('data-step'));
      mark(n, 'wait', x.textContent);
      return n;
    }));
    nextNow(ol);
    mode(true);
    running = true;
    btn.disabled = true;
    q('[data-pj-ftnote]').textContent = 'Closing this dialog does not stop the run.';
    let r = null;
    try { r = await pm7.fetch('/p/new/run', { form: p }); } catch (err) { r = null; }
    if (!r || !r.ok || !r.body) {
      running = false;
      mode(false);
      q('[data-pj-ftnote]').textContent = '';
      setErr(await pm7.why(r, 'Not created'));
      btn.disabled = false;
      return;
    }
    const reader = r.body.getReader(), dec = new TextDecoder();
    let buf = '', end = null, last = null;
    const take = (line) => {
      const x = json(line, null);
      if (!x) return;
      if (x.end) end = x; else { last = x; step(ol, x); }
    };
    try {
      for (;;) {
        const c = await reader.read();
        if (c.value) buf += dec.decode(c.value, { stream: true });
        let i;
        while ((i = buf.indexOf('\n')) >= 0) { take(buf.slice(0, i).trim()); buf = buf.slice(i + 1); }
        if (c.done) break;
      }
      if (buf.trim()) take(buf.trim());
    } catch (err) { /* the connection broke: the summary says so */ }
    running = false;
    ended = true;
    pm7.$$('li.st-now, li.st-wait', ol).forEach((n) => mark(n, 'not_run', n.querySelector('span').textContent));
    summary(end, last, p.get('name'));
  }

  /* ---------- open / reset ---------- */
  function reset(f) {
    ended = false;
    f.reset();
    mode(false);
    q('[data-pj-steps]').replaceChildren();
    q('[data-pj-sum]').hidden = true;
    q('[data-pj-open]').hidden = true;
    q('[data-pj-create]').hidden = false;
    q('[data-pj-cancel]').textContent = 'Cancel';
    q('[data-pj-ftnote]').textContent = '';
    setErr('');
  }
  function open(opener) {
    const x = dlg(), f = form();
    if (!x || !f) return;
    if (ended && !running) reset(f);
    sync();
    pm7.dialog(x, { opener: opener, focus: running || ended ? null : f.elements.name });
    if (!running && !ended) plan();
  }

  pm7.ready(() => {
    const f = form();
    if (!f) return;
    const changed = (e) => {
      if (running || ended) return;
      if (e.target.matches('[data-pj-kind], [data-pj-mode], [data-pj-git], [name=template], [name=name]')) sync();
      planSoon();
    };
    f.addEventListener('input', changed);
    f.addEventListener('change', changed);
    f.addEventListener('submit', run);
  });
  pm7.pjnew = { open: open };
})();
