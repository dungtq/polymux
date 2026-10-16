/* dt Quick chat menus (PRD.md §6.12), on top of js/qchat.js (pm7.qchat.core): the composer chips and the open
   conversation's ⋯. Every menu is pm7.menu (layers.js: one stack, Esc, outside click, phone bottom sheet); confirms
   are pm7.confirm (a dialog over the panel; it closes alone).
   Chips ([data-q-chip=model|mode|mcp|skills]): Model (the core's list; "" = claude's own default); Tools Text only /
   Read / Full + the folder (project names; Full asks once; both fixed once the conversation has a message or for btw);
   MCP servers and Skills (tick one per pick; All / None).
   ⋯ ([data-q-more]): Rename (a dialog with the title field; POST /qchat/convs/<id>/rename), Copy as Markdown
   (GET /qchat/convs/<id>/export into the clipboard; a ClipboardItem promise where the browser has one, so Safari keeps
   the click's permission across the fetch), Delete (typed confirm: the conversation's title, V7-SEC-6; POST
   .../delete), Delete all conversations (typed "delete", as Settings › Quick chat; POST /qchat/delete-all). Toasts
   only after the server said OK; errors show the server's message (pm7.fail). */
(function () {
  'use strict';
  const pm7 = window.pm7, d = document;
  const PROFILE = {
    none: ['Text only', 'chat', 'no files, no commands'],
    read: ['Read in a folder', 'folder', 'Read, Grep, Glob, web'],
    full: ['Full: edits and commands', 'warn', 'without asking']
  };
  const core = () => pm7.qchat.core;

  /* ---------- chips ---------- */
  function modelMenu(b) {
    const c = core(), k = c.cfg(), models = c.opts().models || [''];
    pm7.menu(b, models.map((m) => ({ label: m || 'default model', icon: (k.model || '') === m ? 'check' : 'sparkle',
      note: m ? '' : 'claude’s own', run: () => { k.model = m; c.chips(); } })), { label: 'Model', align: 'start' });
  }
  function modeMenu(b) {
    const c = core(), k = c.cfg(), o = c.opts(), lock = c.locked();
    const items = (o.profiles || ['none']).filter((p) => PROFILE[p]).map((p) => ({
      label: PROFILE[p][0], icon: k.profile === p ? 'check' : PROFILE[p][1], danger: p === 'full',
      note: lock ? (k.profile === p ? 'fixed' : '') : PROFILE[p][2], disabled: lock && k.profile !== p,
      run: () => { pickProfile(p, b); } }));
    const fol = o.folders || [];
    if (fol.length) items.push({ sep: true });
    fol.forEach((f) => items.push({ label: f, icon: f === k.folder ? 'check' : 'folder',
      disabled: lock && f !== k.folder, note: lock && f === k.folder ? 'fixed' : '',
      run: () => { k.folder = f; c.chips(); } }));
    pm7.menu(b, items, { label: 'Tools and folder', align: 'start' });
  }
  async function pickProfile(p, b) {
    const c = core(), k = c.cfg();
    if (p === 'full' && k.profile !== 'full') {
      const ok = await pm7.confirm({ title: 'Full tools for this conversation?', ok: 'Use full tools', danger: true,
        opener: b, body: 'Claude may edit files and run commands in ' + (k.folder || c.opts().default_folder ||
          'the chosen folder') + ' without asking. It stays fixed for the whole conversation once you send.' });
      if (!ok) return;
    }
    k.profile = p;
    if (p !== 'none' && !k.folder) k.folder = c.opts().default_folder || '';
    c.chips();
  }
  function pickMenu(b, kind) {
    const c = core(), k = c.cfg(), all = c.opts()[kind] || [];
    if (!Array.isArray(k[kind])) k[kind] = [];
    const sel = k[kind], set = (names) => { sel.splice(0, sel.length, ...names); c.chips(); };
    const on = (n) => sel.indexOf(n) >= 0;
    const items = all.map((x) => ({ label: x.name, icon: on(x.name) ? 'check' : 'plus', note: x.source || '',
      run: () => { set(on(x.name) ? sel.filter((n) => n !== x.name) : sel.concat(x.name)); } }));
    if (all.length > 1) {
      items.push({ sep: true }, { label: 'All', icon: 'list', run: () => set(all.map((x) => x.name)) },
        { label: 'None', icon: 'x', run: () => set([]) });
    }
    if (!all.length) items.push({ label: kind === 'mcp' ? 'No MCP server switched on' : 'No skill switched on',
      disabled: true, note: 'Settings › Quick chat' });
    pm7.menu(b, items, { label: kind === 'mcp' ? 'MCP servers' : 'Skills', align: 'start' });
  }

  /* ---------- ⋯ of the open conversation ---------- */
  async function rename(cv, b) {
    const inp = d.createElement('input');
    inp.type = 'text';
    inp.value = cv.title || '';
    inp.maxLength = 120;
    inp.setAttribute('aria-label', 'Title');
    inp.addEventListener('keydown', (e) => {
      if (e.key !== 'Enter' || e.isComposing) return;
      e.preventDefault();
      const ok = d.querySelector('#x-dlg .ft .x-btn.pri');
      if (ok) ok.click();
    });
    const asked = pm7.confirm({ title: 'Rename the conversation', body: inp, ok: 'Rename', opener: b });
    requestAnimationFrame(() => { inp.focus(); inp.select(); });
    if (!(await asked)) return;
    const t = inp.value.replace(/\s+/g, ' ').trim();
    if (!t || t === cv.title) return;
    let r = null;
    const url = '/qchat/convs/' + encodeURIComponent(cv.id) + '/rename';
    try { r = await pm7.fetch(url, { form: { title: t } }); } catch (e) { r = null; }
    if (!r || !r.ok) { pm7.fail('Rename failed', r); return; }
    let j = {};
    try { j = await r.json(); } catch (e) { j = {}; }
    core().setTitle(j.title || t);
    pm7.toast('Renamed');
  }
  function copyMd(cv) {
    const url = '/qchat/convs/' + encodeURIComponent(cv.id) + '/export';
    const get = pm7.fetch(url).then((r) => { if (!r.ok) throw r; return r.text(); });
    const ok = () => pm7.toast('Copied as Markdown');
    const no = (r) => {
      if (r && r.status) pm7.fail('Export failed', r); else pm7.toast('Copy failed', '', { error: true });
    };
    const cb = navigator.clipboard;
    if (!cb) { no(null); return; }
    if (window.ClipboardItem && cb.write) {
      const blob = get.then((t) => new Blob([t], { type: 'text/plain' }));
      cb.write([new window.ClipboardItem({ 'text/plain': blob })]).then(ok, () => get.then((t) => cb.writeText(t))
        .then(ok, no));
      return;
    }
    get.then((t) => cb.writeText(t)).then(ok, no);
  }
  async function gone(url, what) {   // POST, then the list again; true after the server said OK
    let r = null;
    try { r = await pm7.fetch(url, { form: {} }); } catch (e) { r = null; }
    if (!r || !r.ok) { pm7.fail(what + ' failed', r); return null; }
    let j = null;
    try { j = await r.json(); } catch (e) { j = null; }
    return j || {};
  }
  async function del(cv, b) {
    const name = String(cv.title || '').trim() || 'New chat';
    const ok = await pm7.confirm({ title: 'Delete the conversation “' + name + '”?', ok: 'Delete', danger: true,
      typed: name, opener: b, body: 'It is removed from the Quick chat history.' });
    if (!ok || !await gone('/qchat/convs/' + encodeURIComponent(cv.id) + '/delete', 'Delete')) return;
    pm7.toast('Deleted “' + name + '”');
    const c = core(), cur = c.conv();
    if (cur && cur.id === cv.id) c.newConv();
    c.refreshList();
  }
  async function delAll(b) {
    const ok = await pm7.confirm({ title: 'Delete all Quick chat conversations? Type delete', ok: 'Delete all',
      danger: true, typed: 'delete', opener: b,
      body: 'Every conversation in the Quick chat history is removed, btw answers included.' });
    if (!ok) return;
    const j = await gone('/qchat/delete-all', 'Delete all');
    if (!j) return;
    const n = +j.deleted || 0;
    pm7.toast('Deleted ' + n + ' conversation' + (n === 1 ? '' : 's'));
    core().newConv();
    core().refreshList();
  }
  function convMenu(b) {
    const cv = core().conv();
    if (!cv || !cv.id) return;
    pm7.menu(b, [
      { label: 'Rename', icon: 'edit', run: () => { rename(cv, b); } },
      { label: 'Copy as Markdown', icon: 'copy', run: () => copyMd(cv) },
      { sep: true },
      { label: 'Delete…', icon: 'trash', danger: true, run: () => { del(cv, b); } },
      { label: 'Delete all conversations…', icon: 'trash', danger: true, run: () => { delAll(b); } }
    ], { label: 'Conversation actions' });
  }

  pm7.ready(() => {
    const p = d.getElementById('q-panel');
    if (!p || !pm7.qchat) return;
    p.addEventListener('click', (e) => {
      const t = e.target && e.target.closest ? e.target : null;
      const chip = t && t.closest('[data-q-chip]'), more = t && t.closest('[data-q-more]');
      if (more) { convMenu(more); return; }
      if (!chip || chip.disabled) return;
      const w = chip.getAttribute('data-q-chip');
      if (w === 'model') modelMenu(chip);
      else if (w === 'mode') modeMenu(chip);
      else pickMenu(chip, w);
    });
  });
})();
