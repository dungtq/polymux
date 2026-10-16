/* dt Settings page: the core's forms and buttons (docs/themes/dt/PRD.md §6.11 "save on change with a toast, no Save
   buttons"). Markup: templates/settings/_work.html, _security.html, _settings_qchat.html, _autoexit.html. Owns no
   global; uses pm7.settings.post / refresh (js/settings.js), pm7.menus.exec (js/parts.js), pm7.reply / fail.
   form[data-s-form] (core: /settings/prefs, /settings/composer, /settings/autoexit, /settings/qchat part=…): any change
     inside posts the WHOLE form (Accept JSON: {ok, notice}); toast after ok; on a refusal the server's message and
     the section reloads (the controls show what is stored again).
   form[data-s-submit] (Add server, Add MCP server): Submit posts it, then reloads data-s-refresh (else the section) and
     fires s:submitted {form, ok, name} on document (js/settings-servers.js chains add → scan → pin).
   button[data-s-do] = a ⋯ menu item as JSON (pm.web.dt.menus): pm7.menus.exec (confirm / typed confirm, POST,
     toast, refresh). Switches with data-s-toggle (Quick chat MCP / skills): POST {name, on}, 204.
   Composer upload types: chips [data-s-ext] with [data-s-untag], the add field [data-s-addtag] (change = Enter or
     leaving it), all kept in the hidden input exts. Passkey: classic passkey.js fires passkey-registered on <body>
     -> the Access section reloads. Quick chat: the chosen sub-tab is kept across its reloads. */
(function () {
  'use strict';
  const pm7 = window.pm7, d = document;
  const REFUSED = /^(exe|sh|bat|ps1|py|js)$/;
  const secOf = (el) => el && el.closest ? el.closest('.s-sec') : null;
  const S = () => pm7.settings;

  function fields(f) {
    const p = new URLSearchParams();
    new FormData(f).forEach((v, k) => { if (typeof v === 'string') p.append(k, v); });
    return p;
  }
  async function saveForm(f) {
    const what = f.getAttribute('data-s-what') || 'Settings';
    const r = await S().post(f.getAttribute('action'), fields(f));
    const a = await pm7.reply(r);
    if (!a.ok) { await pm7.fail(what + ' not saved', r); S().refresh(f.closest('#s-qchat') || secOf(f)); return; }
    pm7.toast(a.notice || 'Saved · ' + what);
  }
  async function submit(f) {
    const what = f.getAttribute('data-s-what') || 'Settings', name = (f.elements.name && f.elements.name.value) || '';
    const r = await S().post(f.getAttribute('action'), fields(f));
    const a = await pm7.reply(r);
    if (!a.ok) { pm7.fail(what + ' not added', r); return; }
    if (f.elements.headers) f.elements.headers.value = '';  // write-only: never kept in the page
    pm7.toast(a.notice || what + ' added');
    d.dispatchEvent(new CustomEvent('s:submitted', { detail: { form: f.id, ok: true, name: name } }));
    S().refresh(f.getAttribute('data-s-refresh') || secOf(f));
  }

  /* ---------- upload types (composer) ---------- */
  function exts(box) { return pm7.$$('[data-s-ext]', box).map((x) => x.getAttribute('data-s-ext')); }
  function store(box) {
    const f = box.closest('form'), h = f && f.elements.exts;
    if (!h) return;
    h.value = exts(box).join(',');
    h.dispatchEvent(new Event('change', { bubbles: true }));
  }
  function addExt(input) {
    const box = input.closest('[data-s-tags]'), v = input.value.trim().replace(/^\./, '').toLowerCase();
    input.value = '';
    if (!box || !v) return;
    if (!/^[a-z0-9]{1,8}$/.test(v)) { pm7.toast('A file type is 1-8 letters or digits', '', { error: true }); return; }
    if (REFUSED.test(v)) { pm7.toast('.' + v + ' is always refused', '', { error: true }); return; }
    if (exts(box).indexOf(v) >= 0) return;
    const x = pm7.el('button.p-hit', { type: 'button', 'data-s-untag': '', 'aria-label': 'Remove ' + v,
      title: 'Remove ' + v }, pm7.icon('x'));
    box.insertBefore(pm7.el('span.s-tag', { 'data-s-ext': v }, v, x), input);
    store(box);
    input.focus();
  }

  /* ---------- events ---------- */
  d.addEventListener('change', (e) => {
    const t = e.target;
    if (!t || !t.closest) return;
    if (t.matches('[data-s-addtag]')) { addExt(t); return; }
    const sw = t.matches('input[type=hidden]') && t.parentNode ? pm7.$('button.sw[data-s-toggle]', t.parentNode) : null;
    if (sw) { toggle(sw, t.value === '1'); return; }
    const f = t.closest('form[data-s-form]');
    if (f) saveForm(f);
  });
  async function toggle(sw, on) {
    const name = sw.getAttribute('data-s-name') || '';
    const r = await S().post(sw.getAttribute('data-s-toggle'), { name: name, on: on ? '1' : '0' });
    const a = await pm7.reply(r);
    if (!a.ok) {
      sw.setAttribute('aria-checked', on ? 'false' : 'true');
      pm7.fail(name + ' not changed', r);
      return;
    }
    pm7.toast('Saved · ' + name + (on ? ' on' : ' off'));
  }
  d.addEventListener('submit', (e) => {
    const f = e.target;
    if (!f || !f.matches) return;
    if (f.matches('form[data-s-submit]')) { e.preventDefault(); submit(f); return; }
    if (f.matches('form[data-s-form]')) e.preventDefault();  // saved on change; Enter never posts the page
  });
  d.addEventListener('click', (e) => {
    const t = e.target && e.target.closest ? e.target : null;
    if (!t) return;
    const b = t.closest('button[data-s-do]');
    if (b) {
      let it = null;
      try { it = JSON.parse(b.getAttribute('data-s-do')); } catch (err) { it = null; }
      if (it) pm7.menus.exec(b, it);
      return;
    }
    const u = t.closest('[data-s-untag]');
    if (u) {
      const box = u.closest('[data-s-tags]'), tag = u.closest('[data-s-ext]');
      if (box && tag && exts(box).length > 1) { tag.remove(); store(box); }
      else pm7.toast('Keep at least one file type', '', { error: true });
      return;
    }
    const add = t.closest('[data-s-addmcp]');
    if (add) {
      const f = d.getElementById(add.getAttribute('aria-controls'));
      if (!f) return;
      f.hidden = !f.hidden;
      add.setAttribute('aria-expanded', f.hidden ? 'false' : 'true');
      if (!f.hidden && f.elements.name) f.elements.name.focus();
    }
  });
  d.body.addEventListener('passkey-registered', () => S().refresh('#s-sec-access'));

  /* Quick chat: keep the chosen tab across the fragment's reloads */
  let qtab = '';
  d.addEventListener('pm7:tab', (e) => { if (e.target.closest && e.target.closest('#s-qchat')) qtab = e.detail.key; });
  d.addEventListener('htmx:afterSettle', (e) => {
    const q = d.getElementById('s-qchat');
    if (!qtab || !q || !(e.target === q || q.contains(e.target))) return;
    const tab = d.getElementById('p-tab-' + qtab);
    if (tab && tab.getAttribute('aria-selected') !== 'true') tab.click();
  });
})();
