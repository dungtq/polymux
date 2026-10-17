/* dt Agents page (PRD.md §6.8): the review → apply flow of every change, Re-scan, the login column and the
   page refresh after a change. Markup: templates/agents.html, agents_detail.html, _agents_*.html, agents/*.html; view
   models pm.web.dt.agents*.
   A plan (JSON, from the view model) = {fields: [[name, value], ...] (action first; a list field repeats its name),
   title, ok, done, typed?, undo?, quiet?, after?}. run(plan): POST /agents/review (fetch → JSON {ok, ops, token,
   destructive}) → pm7.confirm listing the exact file operations (typed = the name to type; undo = its last sentence,
   e.g. where the backup goes) → POST /agents/apply with the token →
   toast `done` after the server's OK (V7-CODE-5) → the page is rendered again. A 409 (the files changed since the
   review) says so and reviews again; other refusals toast the server's message (pm7.fail). quiet = no dialog (the
   skills switch: a reversible link).
   Hooks: ⋯ items {event: "a:plan"} with the row's data-a-plans = {item id: plan}; {event: "a:sync"} opens #a-sync;
     {event: "a:view"} opens the row's data-a-file path in the markdown reader (pm7.md.file, read-only);
     button[data-a-run] = a plan (Findings Fix, the editor's Remove); button[data-a-fixall=<kind>] = every Fix of that
     band in one dialog, applied in order; form[data-a-plan] (editor, #a-new, #a-sync: data-a-title / -ok / -done /
     -after) = a plan from its fields, errors in its [data-a-ferr]; [data-a-open=<dialog id>] opens a dialog;
     button.sw[data-a-sw] (skills) = a quiet plan, enable from the switch, flipped back on a refusal;
     [data-a-rescan] = POST /agents/rescan, logins re-probed, page again; [data-a-login=<agent>] cells = the login
     state from GET /agents/auth.json (the core's status cache; no probe on render).
   Refresh: GET the page URL (no HX header: the whole page), replace #a-page, htmx.process, table state restored; a
   different URL (the editor's `after`) replaces the address (history.replaceState). Not an hx-get on #a-page fired
   by pm7:refresh: an htmx GET of /agents?t=library or /agents/<name>?s=skills gets only the list fragment from the
   core (#a-lib / #a-sk, which do refresh on pm7:refresh), and a plan's `after` may be another URL. */
(function () {
  'use strict';
  const pm7 = window.pm7, d = document, el = pm7.el;

  function params(fields, extra) {
    const p = new URLSearchParams();
    (fields || []).concat(extra || []).forEach((f) => { p.append(String(f[0]), f[1] == null ? '' : String(f[1])); });
    return p;
  }
  async function json(r) { return (await pm7.reply(r)).json; }  // the JSON object of an answer (pm7.reply, core.js)
  function say(box, text) {
    if (box) { box.textContent = text; box.hidden = false; } else pm7.toast(text, '', { error: true });
  }

  /* ---------- review / apply ---------- */
  async function review(fields, what, box) {
    let r = null;
    try { r = await pm7.fetch('/agents/review', { form: params(fields) }); } catch (e) { r = null; }
    if (!r || !r.ok) { say(box, await pm7.why(r, what)); return null; }
    const j = await json(r);
    if (!j || !j.ok) { say(box, what + ': ' + ((j && j.error) || 'no plan')); return null; }
    return j;
  }
  async function apply(fields, token, what) {
    let r = null;
    try { r = await pm7.fetch('/agents/apply', { form: params(fields, [['token', token]]) }); } catch (e) {
      r = null;
    }
    const j = r ? await json(r) : null;
    if (r && r.status === 409) return { stale: true, msg: (j && j.error) || 'The files changed since the review.' };
    if (!r || !r.ok) { pm7.fail(what, r); return { ok: false }; }
    if (!j || !j.ok) {
      pm7.toast(what + ': ' + ((j && j.error) || 'failed'), '', { error: true });
      return { ok: false };
    }
    return { ok: true, lines: j.lines || [] };
  }
  function opsList(ops) {
    return el('ul.a-ops', null, ops.map((o) => el('li', null, el('span.a-opk', null, o.kind), ' ',
      el('code', null, o.path), o.detail ? el('small', null, o.detail) : null)));
  }
  function opsBody(groups, destructive) {
    const n = groups.reduce((s, g) => s + g.ops.length, 0);
    const kids = [el('p', null, n + ' file operation' + (n === 1 ? '' : 's') + '. Nothing has changed yet; Apply '
      + (destructive ? 'backs the files up before it removes them' : 'keeps a backup of every file it changes')
      + ' (each operation says where).')];
    groups.forEach((g) => {
      if (groups.length > 1) kids.push(el('p.a-opsh', null, g.title));
      kids.push(opsList(g.ops));
    });
    return el('div.a-opsb', null, kids);
  }

  /* one planned change: review, show, apply; true when applied */
  async function run(plan, opener, box) {
    const what = plan.title || 'Change';
    for (let tries = 0; tries < 3; tries++) {
      const pl = await review(plan.fields, what, box);
      if (!pl) return false;
      if (!pl.ops || !pl.ops.length) { pm7.toast('Nothing to change'); return false; }
      if (!plan.quiet) {
        const ok = await pm7.confirm({ title: what, body: opsBody([{ ops: pl.ops }], pl.destructive),
          ok: plan.ok || 'Apply', danger: !!pl.destructive, typed: plan.typed, undo: plan.undo,
          opener: opener });
        if (!ok) return false;
      }
      const a = await apply(plan.fields, pl.token, what);
      if (a.stale) {
        pm7.toast(a.msg + (plan.quiet ? '' : ' Showing the new plan.'), '', { error: true });
        if (plan.quiet) return false;
        continue;
      }
      if (!a.ok) return false;
      const dl = opener && opener.closest ? opener.closest('dialog') : null;
      if (dl && pm7.layers) pm7.layers.close(dl);
      pm7.toast(plan.done || 'Applied');
      await refresh(plan.after);
      return true;
    }
    return false;
  }

  /* every Fix of a Findings band: one dialog with all operations, then the applies in order */
  async function runAll(plans, title, opener) {
    const got = [];
    for (const p of plans) {
      const pl = await review(p.fields, p.title || 'Fix');
      if (!pl) return;
      if (pl.ops && pl.ops.length) got.push({ plan: p, pl: pl, title: p.title, ops: pl.ops });
    }
    if (!got.length) { pm7.toast('Nothing to change'); return; }
    const ok = await pm7.confirm({ title: title, body: opsBody(got, got.some((g) => g.pl.destructive)),
      ok: 'Apply all ' + got.length, opener: opener });
    if (!ok) return;
    let done = 0;
    for (const g of got) {
      const a = await apply(g.plan.fields, g.pl.token, g.title);
      if (a.stale) { pm7.toast(a.msg + ' Review the rest again.', '', { error: true }); break; }
      if (!a.ok) break;
      done++;
    }
    if (done) pm7.toast('Applied ' + done + ' of ' + got.length + ' fixes');
    await refresh();
  }

  /* ---------- the page again from the server ---------- */
  async function refresh(url) {
    const cur = window.location.pathname + window.location.search;
    const to = url && url[0] === '/' && url[1] !== '/' ? url : cur;
    let r = null;
    try { r = await pm7.fetch(to); } catch (e) { r = null; }
    const doc = r && r.ok ? new DOMParser().parseFromString(await r.text(), 'text/html') : null;
    const fresh = doc && doc.getElementById('a-page'), now = d.getElementById('a-page');
    if (!fresh || !now) { if (to !== cur) window.location.assign(to); return; }
    const node = d.importNode(fresh, true);
    now.replaceWith(node);
    if (to !== cur) window.history.replaceState(null, '', to);  // the editor's tab: Back skips the editor
    if (doc.title) d.title = doc.title;
    if (window.htmx) window.htmx.process(node);
    if (pm7.table && pm7.table.restore) pm7.table.restore(node);
    if (pm7.keys && pm7.keys.fill) pm7.keys.fill(node);
    logins(false);
  }

  /* ---------- login column (Installed) ---------- */
  const LOGIN = { in: ['logged in', 'ok'], out: ['not logged in', 'warn'] };
  async function logins(fresh) {
    const cells = pm7.$$('[data-a-login]');
    if (!cells.length) return;
    let r = null;
    try { r = await pm7.fetch('/agents/auth.json' + (fresh ? '?fresh=1' : '')); } catch (e) { r = null; }
    const j = r && r.ok ? await json(r) : null;
    const by = {};
    ((j && j.agents) || []).forEach((a) => { by[a.name] = a; });
    cells.forEach((c) => {
      const a = by[c.getAttribute('data-a-login')];
      let text = 'unknown', cls = 'muted', title = 'Not checked yet: open the agent or Re-scan';
      if (a && !a.supported) { text = '–'; title = 'pm cannot check this agent\'s login'; }
      else if (a && LOGIN[a.state]) {
        text = LOGIN[a.state][0]; cls = LOGIN[a.state][1];
        title = [a.account, a.method, a.plan].filter(Boolean).join(' · ') || text;
      } else if (a && a.error) title = a.error;
      else if (!j) title = 'The login state could not be read';
      c.textContent = text;
      c.className = cls;
      c.title = title;
    });
  }

  /* ---------- hooks ---------- */
  function parse(s) {
    try { const v = JSON.parse(s || ''); return v && v.fields ? v : null; } catch (e) { return null; }
  }
  function formPlan(f) {
    const fields = [];
    new FormData(f).forEach((v, k) => { if (k !== 'csrf' && typeof v === 'string') fields.push([k, v]); });
    const name = f.elements.name ? f.elements.name.value : '';
    return { fields: fields, title: (f.getAttribute('data-a-title') || 'Change') + (f.closest('dialog') && name
      ? ' ' + name : ''), ok: f.getAttribute('data-a-ok') || 'Apply', done: f.getAttribute('data-a-done') || 'Applied',
      after: f.getAttribute('data-a-after') || '' };
  }
  async function busy(b, fn) {
    if (b) b.disabled = true;
    try { return await fn(); } finally { if (b && d.contains(b)) b.disabled = false; }
  }
  /* the head's Re-scan: POST /agents/rescan drops the core's inventory cache, then the logins are re-probed and the
     page rendered again (an apply drops the cache itself, so a change needs no re-scan) */
  async function rescan() {
    let r = null;
    try { r = await pm7.fetch('/agents/rescan', { method: 'POST', form: {} }); } catch (e) { r = null; }
    if (!r || !r.ok) { pm7.fail('Re-scan', r); return; }
    try { await pm7.fetch('/agents/auth.json?fresh=1'); } catch (e) { /* the inventory is read again anyway */ }
    await refresh();
    pm7.toast('Re-scanned the agents');
  }
  function flipBack(sw) {
    const on = sw.getAttribute('aria-checked') !== 'true';
    sw.setAttribute('aria-checked', on ? 'true' : 'false');
    const i = sw.parentNode && sw.parentNode.querySelector('input[type=hidden]');
    if (i) i.value = on ? '1' : '0';
  }
  async function toggle(input) {
    const sw = input.parentNode && input.parentNode.querySelector('button.sw[data-a-sw]');
    const p = sw && parse(sw.getAttribute('data-a-sw'));
    if (!p) return;
    const on = input.value === '1';
    p.fields.push(['enable', on ? '1' : '0']);
    p.done = String(p.done || '{}').replace('{}', on ? 'Enabled' : 'Disabled');
    p.title = (on ? 'Enable ' : 'Disable ') + p.title;
    const ok = await busy(sw, () => run(p, sw));
    if (!ok && d.contains(sw)) flipBack(sw);
  }

  d.addEventListener('a:plan', (e) => {
    const t = e.target, det = e.detail || {}, row = t && t.closest ? t.closest('[data-a-plans]') : null;
    let plans = null;
    try { plans = row ? JSON.parse(row.getAttribute('data-a-plans')) : null; } catch (err) { plans = null; }
    const p = plans && det.item ? plans[det.item] : null;
    if (p) run(p, t);
  });
  d.addEventListener('a:view', (e) => {
    const t = e.target, row = t && t.closest ? t.closest('[data-a-file]') : null;
    const ok = row && pm7.md && pm7.md.file(row.getAttribute('data-a-file'));
    if (!ok) pm7.toast('Nothing to show', '', { error: true });
  });
  d.addEventListener('a:sync', (e) => {
    const dl = d.getElementById('a-sync');
    if (dl) pm7.dialog(dl, { opener: e.target });
  });
  d.addEventListener('click', (e) => {
    const t = e.target && e.target.closest ? e.target : null;
    if (!t) return;
    const b = t.closest('[data-a-run], [data-a-fixall], [data-a-open], [data-a-rescan]');
    if (!b || !b.closest('#a-page') || b.disabled) return;
    e.preventDefault();
    if (b.hasAttribute('data-a-rescan')) { busy(b, rescan); return; }
    if (b.hasAttribute('data-a-open')) {
      const dl = d.getElementById(b.getAttribute('data-a-open'));
      if (dl) pm7.dialog(dl, { opener: b });
      return;
    }
    if (b.hasAttribute('data-a-run')) {
      const p = parse(b.getAttribute('data-a-run'));
      if (p) busy(b, () => run(p, b));
      return;
    }
    const grp = b.closest('.p-gg');
    const plans = grp ? pm7.$$('[data-a-run]', grp).map((x) => parse(x.getAttribute('data-a-run'))).filter(Boolean)
      : [];
    const label = grp ? (pm7.$('.p-fold .t', grp) || {}).textContent || 'Findings' : 'Findings';
    if (plans.length) busy(b, () => runAll(plans, label + ': fix all ' + plans.length, b));
  });
  d.addEventListener('submit', (e) => {
    const f = e.target;
    if (!f || !f.matches || !f.matches('form[data-a-plan]') || !f.closest('#a-page')) return;
    e.preventDefault();
    if (!f.reportValidity()) return;
    const box = pm7.$('[data-a-ferr]', f);
    if (box) box.hidden = true;
    const sub = pm7.$('[type=submit]', f);
    busy(sub, () => run(formPlan(f), sub || f, box));
  });
  d.addEventListener('change', (e) => {
    const i = e.target;
    if (i && i.matches && i.matches('input[type=hidden][name=enable]') && i.closest('#a-sk')) toggle(i);
  });
  pm7.agents = { run: run, refresh: refresh };
  pm7.ready(() => logins(false));
})();
