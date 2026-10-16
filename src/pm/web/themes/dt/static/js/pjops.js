/* dt theme (pm v7) project view, operations tabs: what the Git, Stack and VMs tabs share (js/pjgit.js, pjstack.js,
   pjvms.js; templates/project/_tab_{git,stack,vms}.html; look css/pjops.css). Each tab partial loads this file, so it
   runs once (guard) however many partials the frame includes.
   Globals: pm7.pjops = {
     h(spec, attrs, ...kids) -> element       = pm7.el (core.js), add = pm7.add: kept here for the tab files
     btn(label, o) · ib(icon, label, o) · menuBtn(label, items | () => items)   x-btn.sm / x-ib.sm / the ⋯ trigger
                                              (pm7.menu items; the trigger is .keep, so phones keep it)
     table(key, label, cls, rows, o) · gh(cols) · row(id, cells, o) · cell(kids, cls) · dot(state, word) ·
     name(text, o) · end(ts, text) · meta(kids) · acts(kids) · detBtn(table, id, label, o) · det(table, id, label) ·
     empty(text)                              the grid table of parts/_table.html (PARTS.md §6), same markup
                                              and classes, so table.css, table.js (details, X) and listkeys.js apply
     h2(text, count, note) · ban(tone, text, actions) · since(ts)   section heading, guard line, data-since value
     result(r, what) -> {ok, text, clash, files}   a POST answer: git JSON {ok, text, clash} | an HTML fragment
                                              <p class="ok">text</p> (stack, VMs, fetch / pull / push) | an error
     post(url, form, what) -> result          POST with CSRF (pm7.fetch; array values = repeated fields); toast the
                                              server's text after OK
                                              (V7-CODE-5), its message on failure (pm7.why) · params(form)
     tab(key, load(root, again))              load when the frame shows the tab (pm7:pj-tab {key} on document) or when
                                              it is shown at first paint; again on pm:refresh / pm7:refresh while it
                                              is shown. No timer (V7-CODE-7). }
   Hooks: [data-pjops=<key>][data-pj-base="/p/<project>"][data-pj-name] = the partial's root, inside the frame's tab
   panel (section.pj-tab[data-pj-tab=<key>], [hidden] while another tab shows). */
(function () {
  'use strict';
  const pm7 = window.pm7, d = document;
  if (!pm7 || pm7.pjops) return;

  const h = pm7.el, add = pm7.add;  // core.js: the one DOM builder
  const icon = (name) => (name && pm7.icon ? pm7.icon(name) : null);

  /* ---------- controls ---------- */
  function btn(label, o) {
    o = o || {};
    const b = h('button.x-btn.sm', Object.assign({ type: 'button', cls: o.cls, title: o.title,
      'aria-label': o.aria, disabled: !!o.disabled, on: o.run ? { click: o.run } : null }, o.attrs || {}));
    add(b, [icon(o.icon), h('span.lb', null, label)]);
    return b;
  }
  function ib(name, label, o) {
    o = o || {};
    const b = h('button.x-ib.sm', Object.assign({ type: 'button', cls: o.cls, title: o.title || label,
      'aria-label': label, disabled: !!o.disabled, on: o.run ? { click: o.run } : null }, o.attrs || {}));
    add(b, icon(name));
    return b;
  }
  function menuBtn(label, items) {
    const b = ib('more', label, { cls: 'p-mbtn keep', title: 'More', attrs: { 'aria-haspopup': 'menu',
      'aria-expanded': 'false' } });
    b.addEventListener('click', (e) => {
      e.preventDefault();
      pm7.menu(b, typeof items === 'function' ? items() : items, { label: label });
    });
    return b;
  }

  /* ---------- grid table (parts/_table.html markup) ---------- */
  const domid = (t, id) => ('p-det-' + t + '-' + id).replace(/ /g, '_');
  function table(key, label, cls, rows, o) {
    o = o || {};
    const a = { cls: cls, role: 'table', 'aria-label': label, 'data-p-table': key, 'data-list': o.list };
    Object.keys(o.keys || {}).forEach((k) => { a['data-key-' + k] = o.keys[k]; });
    Object.keys(o.labels || {}).forEach((k) => { a['data-key-' + k + '-label'] = o.labels[k]; });
    return h('div.p-g', a, rows);
  }
  function gh(cols) {
    return h('div.p-gh', { role: 'row' }, cols.map((c) => h('span', { role: 'columnheader',
      cls: ((c.cls || '') + (c.num ? ' num' : '')).trim() || null, title: c.title },
    c.node || c.label || '', c.sr ? h('span.vh', null, c.sr) : null)));
  }
  function row(id, cells, o) {
    o = o || {};
    return h('div.p-gr', Object.assign({ role: 'row', 'data-row-id': id, tabindex: o.cur ? '0' : '-1',
      cls: [o.cls, o.open ? 'open' : ''].join(' '), title: o.title,
      'data-p-q': o.q != null ? String(o.q).toLowerCase() : null }, o.attrs || {}), cells);
  }
  const cell = (kids, cls) => h('span', { role: 'cell', cls: cls }, kids);
  function dot(state, word) {
    return h('span.gc-dot', { role: 'cell' }, h('i', { cls: 'dot ' + (state || 'unknown'), title: word || state,
      'aria-hidden': 'true' }), h('span.vh', null, word || state));
  }
  function name(text, o) {
    o = o || {};
    const nm = h('span.nm', { cls: o.sfx ? 'sfx' : null, title: o.title }, o.sfx ? h('bdi', null, text) : text);
    return h('span.gc-nm', { role: 'cell' }, nm, o.chips || null);
  }
  const end = (ts, text) => h('span.gc-end', { role: 'cell', 'data-since': since(ts) }, text != null ? text : ago(ts));
  const meta = (kids) => h('span.gc-m', { role: 'cell' }, kids);
  const acts = (kids) => h('span.gc-act', { role: 'cell' }, kids);
  function detBtn(t, id, label, o) {
    o = o || {};
    const b = h('button.x-ib.sm', { type: 'button', cls: o.cls || 'p-detb', 'data-p-det-btn': true,
      'aria-expanded': 'false', 'aria-controls': domid(t, id), 'aria-label': label, title: o.title || 'Details',
      'data-k-title': 'X' });
    add(b, icon(o.icon || 'down'));
    return b;
  }
  function det(t, id, label) {
    return h('div.p-det', { role: 'row', id: domid(t, id), 'data-p-det-for': id, hidden: true },
      h('div.p-detc', { role: 'cell', 'aria-label': label || id }));
  }
  const empty = (text) => h('div.p-empty', { role: 'row' }, h('span', { role: 'cell' }, text));

  /* ---------- section parts ---------- */
  function h2(text, count, note) {
    return h('h2.p-h2', null, text, count != null ? h('span.c', null, String(count)) : null,
      note ? h('span.note', null, note) : null);
  }
  function ban(tone, text, actions) {
    return h('div.pj-guard', { cls: tone, role: 'status' }, tone === 'note' ? null : icon('warn'),
      h('span.t', null, text), actions && actions.length ? h('span.a', null, actions) : null);
  }
  function since(ts) {
    const n = Number(ts);
    return n > 0 && isFinite(n) ? String(Math.floor(n)) : null;
  }
  function ago(ts) {
    const n = Number(ts);
    return n > 0 && pm7.ticker ? pm7.ticker.age(Date.now() / 1000 - n) : '';
  }

  /* ---------- POST answers ---------- */
  function fragText(html) {
    const doc = new DOMParser().parseFromString(String(html || ''), 'text/html');
    return (doc.body.textContent || '').replace(/\s+/g, ' ').trim().slice(0, 300);
  }
  async function result(r, what) {
    if (!r) return { ok: false, text: what + ': no answer', clash: [], files: [] };
    const ct = (r.headers && r.headers.get('Content-Type')) || '';
    if (!r.ok) {
      let files = [];
      if (/json/i.test(ct)) { try { files = (await r.clone().json()).files || []; } catch (e) { files = []; } }
      return { ok: false, text: await pm7.why(r, what), clash: [], files: files };
    }
    if (/json/i.test(ct)) {
      let j = null;
      try { j = await r.json(); } catch (e) { j = null; }
      if (j && j.ok === false) return { ok: false, text: what + ': ' + (j.error || 'failed'), clash: [],
        files: j.files || [] };
      return { ok: true, text: (j && j.text) || '', clash: (j && j.clash) || [], files: [] };
    }
    const body = await r.text();
    return { ok: true, text: /html/i.test(ct) ? fragText(body) : body.trim().slice(0, 300), clash: [], files: [] };
  }
  function params(form) {   // array values become repeated fields (paths=a&paths=b); null / undefined skipped
    const q = new URLSearchParams();
    Object.keys(form || {}).forEach((k) => {
      [].concat(form[k]).forEach((v) => { if (v != null) q.append(k, String(v)); });
    });
    return q;
  }
  async function post(url, form, what) {
    let r = null;
    try { r = await pm7.fetch(url, { form: params(form) }); } catch (e) { r = null; }
    const res = await result(r, what);
    if (res.ok) pm7.toast(res.text || what + ': done');
    else pm7.toast(res.text, '', { error: true });
    return res;
  }

  /* ---------- lazy tabs ---------- */
  function tab(key, load) {
    const sec = () => d.querySelector('[data-pjops="' + key + '"]');
    let seen = false;
    function go(again, force) {
      const s = sec();
      if (!s || (!force && s.closest('[hidden]'))) return;
      seen = true;
      load(s, again);
    }
    d.addEventListener('pm7:pj-tab', (e) => { if (e.detail && e.detail.key === key) go(seen, true); });
    ['pm:refresh', 'pm7:refresh'].forEach((t) => d.addEventListener(t, () => { if (seen) go(true, false); }));
    pm7.ready(() => go(false, false));
  }

  pm7.pjops = { h: h, add: add, icon: icon, btn: btn, ib: ib, menuBtn: menuBtn, table: table, gh: gh, row: row,
    cell: cell, dot: dot, name: name, end: end, meta: meta, acts: acts, detBtn: detBtn, det: det, empty: empty,
    domid: domid, h2: h2, ban: ban, since: since, ago: ago, result: result, params: params, post: post, tab: tab };
})();
