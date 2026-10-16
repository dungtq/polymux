/* dt Quick chat panel (PRD.md §6.12, LAYOUT.md §5 "Quick chat", DESIGN.md §7 "Panel", §8 key Q): one-shot
   `claude -p` conversations beside any page. Markup: shell/_qchat.html (section#q-panel, hooks [data-q-*]); the top-bar
   icon after the brand and the phone sidebar drawer's row are [data-q-open].
   Globals: pm7.qchat = {open(id?), close(), toggle(), isOpen()}.
   Data only through the core's /qchat routes: GET options (models, enabled profiles, folders, MCP servers, skills,
   defaults), GET convs?q= (history, title search), GET convs/<id> (conv + messages), GET convs/<id>/export (Markdown),
   POST convs/<id>/rename (title), POST convs/<id>/delete, POST send (SSE through js/qstream.js).
   Panel: a layer of layers.js (soft, rank 30: Esc closes it after any dialog / menu and before the overlay sidebar);
   no scrim and no outside click, so the page stays usable. Expand docks it right of the sidebar with the tabs visible
   (pm.v7.qchat.dock); the conversation list shows or hides (pm.v7.qchat.side; phones: over the chat, hidden on open).
   Chips (menus: js/qchat-menus.js): model; tools Text only / Read / Full + folder (Full asks once with pm7.confirm;
   tools and folder are fixed once the conversation has a message, as the server does with the folder); MCP and
   Skills (need Read or Full; Skills also with Text only when Settings allows it). btw conversations (an agent's side
   questions) never get tools.
   Answers stream as text and end as the server's rendered, escaped markdown (done.html); tool calls show as chips.
   ⋯ of the open conversation: Rename (a dialog with the title), Copy as Markdown, Delete, Delete all (typed).
   "Open as a tab" is not offered: the tab store has no quick chat kind. /?qchat=<id> opens that conversation on load
   (then history.replaceState drops the parameter). Keys: Q (the shell key table's row "qchat",
   also the > command "Quick chat"; not in text fields), Enter sends, Shift+Enter is a new line (the textarea's own
   listener). */
(function () {
  'use strict';
  const pm7 = window.pm7, d = document;
  const STARTERS = ['Explain this error: ', 'Write a jq one-liner that ', 'Review this diff: '];
  const BANDS = ['Today', 'Yesterday', 'Earlier'];
  let opts = null, convs = [], query = '', C = null, ctl = null, opener = null, listSeq = 0;
  let draft = { model: '', profile: 'none', folder: '', mcp: [], skills: [] };

  const P = () => d.getElementById('q-panel');
  const q = (sel) => { const p = P(); return p ? p.querySelector(sel) : null; };
  const el = pm7.el;  // core.js: el(tag, cls, text)
  function icon(name) { return pm7.icon(name) || el('span'); }
  function isOpen() { const p = P(); return !!p && !p.hidden; }
  function started() { return !!(C && C.msgs > 0); }
  function cfg() { return C || draft; }
  function kindOf(id) { return String(id || '').replace(/-[^-]+$/, '') || 'other'; }

  /* ---------- open, close, dock, side list ---------- */
  function pressed() {
    pm7.$$('[data-q-open]').forEach((b) => b.setAttribute('aria-pressed', isOpen() ? 'true' : 'false'));
  }
  async function open(id) {
    const p = P();
    if (!p) return;
    if (!isOpen()) {
      opener = d.activeElement;
      if (pm7.side && pm7.overlay() && pm7.side.isOpen()) pm7.side.close();
      p.hidden = false;
      dock(pm7.store.get('qchat.dock') === '1' && !pm7.phone());
      side(pm7.phone() ? false : pm7.store.get('qchat.side') !== '0');
      pressed();
      pm7.layers.sync();
    }
    await ensureOpts();
    if (id != null) await openConv(id); else if (!C && !q('[data-q-log]').childElementCount) newConv();
    refreshList();
    if (!pm7.phone()) q('[data-q-input]').focus();
  }
  function close() {
    const p = P();
    if (!p || p.hidden) return;
    p.hidden = true;
    pressed();
    pm7.layers.closeLight();
    if (opener && d.contains(opener) && p.contains(d.activeElement || p)) opener.focus({ preventScroll: true });
    else if (p.contains(d.activeElement)) d.activeElement.blur();
    opener = null;
  }
  function toggle() { if (isOpen()) close(); else open(); }
  function dock(on) {
    P().classList.toggle('dock', on);
    const b = q('[data-q-dock]');
    b.setAttribute('aria-pressed', on ? 'true' : 'false');
    b.setAttribute('aria-label', on ? 'Back to the panel' : 'Expand over the page');
    b.title = b.getAttribute('aria-label');
    b.replaceChildren(icon(on ? 'shrink' : 'expand'));
  }
  function side(on) {
    q('[data-q-sidebar]').hidden = !on;
    q('[data-q-side]').setAttribute('aria-pressed', on ? 'true' : 'false');
  }

  /* ---------- options, history list ---------- */
  async function getJSON(url) {
    let r = null;
    try { r = await pm7.fetch(url); } catch (e) { r = null; }
    if (!r || !r.ok) throw r;
    return r.json();
  }
  async function ensureOpts() {
    if (opts) return;
    try { opts = await getJSON('/qchat/options'); } catch (r) {
      pm7.fail('Quick chat options', r || null);
      opts = null;
    }
    if (opts && !C) draft = fresh();
  }
  function fresh() {
    const o = opts || {}, on = (l) => (l || []).filter((x) => x.on).map((x) => x.name);
    return { model: '', profile: o.default_profile || 'none', folder: o.default_folder || '', mcp: on(o.mcp),
      skills: on(o.skills) };
  }
  async function refreshList() {
    const n = ++listSeq;
    let j = null;
    try { j = await getJSON('/qchat/convs' + (query ? '?q=' + encodeURIComponent(query) : '')); } catch (r) {
      if (n === listSeq) pm7.fail('Quick chat history', r || null);
      return;
    }
    if (n !== listSeq) return;
    convs = (j && j.convs) || [];
    renderList();
  }
  function band(ts) {
    const now = new Date(), t0 = new Date(now.getFullYear(), now.getMonth(), now.getDate()).getTime() / 1000;
    return ts >= t0 ? 0 : ts >= t0 - 86400 ? 1 : 2;
  }
  function age(ts) {
    const t = new Date(ts * 1000), p2 = (n) => (n < 10 ? '0' : '') + n;
    return band(ts) < 2 ? p2(t.getHours()) + ':' + p2(t.getMinutes())
      : t.toLocaleDateString(undefined, { month: 'short', day: 'numeric' });
  }
  function profWord(p) { return p === 'none' ? 'text only' : p; }
  function renderList() {
    const L = q('[data-q-list]'), out = [];
    let g = -1;
    convs.forEach((c) => {
      const b = band(c.updated);
      if (b !== g) { g = b; out.push(el('div', 'q-gl', BANDS[b])); }
      const it = el('button', 'q-it');
      it.type = 'button';
      it.setAttribute('data-q-id', String(c.id));
      if (c.agent) it.setAttribute('data-kind', kindOf(c.agent));
      if (C && C.id === c.id) it.setAttribute('aria-current', 'true');
      it.title = c.title + (c.archived ? ' · its claude session is gone (read-only)' : '');
      it.append(el('span', 'ell', c.agent ? 'btw · ' + c.agent : c.title),
        el('small', 'ell', [c.model || 'default', profWord(c.profile), age(c.updated)].join(' · ')));
      out.push(it);
    });
    if (!convs.length) out.push(el('p', 'q-none', query ? 'Nothing found.' : 'No conversations yet.'));
    L.replaceChildren(...out);
  }

  /* ---------- the conversation: chips, note, messages ---------- */
  function locked() { return started() || !!(C && C.agent); }
  function chips() {
    const k = cfg(), btw = !!(C && C.agent), o = opts || {};
    const set = (name, ic, text, opt) => {
      const b = q('[data-q-chip="' + name + '"]'), x = opt || {};
      b.hidden = btw;
      b.disabled = !!x.off;
      b.classList.toggle('red', !!x.red);
      b.title = x.title || b.getAttribute('data-title') || '';
      b.replaceChildren(...[ic ? icon(ic) : null, el('span', 'ell', text), icon('down')].filter(Boolean));
    };
    ['model', 'mode', 'mcp', 'skills'].forEach((n) => {
      const b = q('[data-q-chip="' + n + '"]');
      if (!b.hasAttribute('data-title')) b.setAttribute('data-title', b.title);
    });
    set('model', 'sparkle', k.model || 'default model');
    const fol = k.folder || 'pick a folder';
    set('mode', k.profile === 'none' ? 'chat' : 'folder', k.profile === 'none' ? 'Text only'
      : (k.profile === 'full' ? 'Full · ' : 'Read · ') + fol, { red: k.profile === 'full',
      title: locked() ? 'Tools and folder are fixed for this conversation' : '' });
    set('mcp', '', 'MCP ' + (k.mcp || []).length, { off: k.profile === 'none',
      title: k.profile === 'none' ? 'MCP needs Read or Full tools' : '' });
    const skOk = k.profile !== 'none' || !!o.skills_in_none;
    set('skills', '', 'Skills ' + (k.skills || []).length, { off: !skOk,
      title: skOk ? '' : 'Skills need Read or Full tools' });
    q('[data-q-btwchip]').hidden = !btw;
    note();
  }
  function note() {
    const k = cfg(), n = q('[data-q-note]'), mono = (t) => el('span', 'mono', t);
    n.classList.toggle('warn', k.profile === 'full' && !(C && C.agent));
    if (C && C.agent) n.replaceChildren('Context: the newest part of ', el('b', '', C.agent),
      '’s chat, masked. Nothing reaches its pane.');
    else if (C && C.archived) n.replaceChildren('Read-only: the claude session of this conversation is gone. ' +
      'Start a new conversation to go on.');
    else if (k.profile === 'none') n.replaceChildren('Text only: no files, no commands, no MCP. Saved to history.');
    else if (k.profile === 'read') n.replaceChildren('Read only in ', mono(k.folder || '(no folder)'),
      ': Read, Grep, Glob, web and the ticked MCP servers. No edits, no commands.');
    else n.replaceChildren(el('b', 'bad', 'Full'), ' in ', mono(k.folder || '(no folder)'),
      ': edits files and runs commands without asking. Fixed for this conversation.');
    const ro = !!(C && C.archived);
    q('[data-q-input]').disabled = ro;
    q('[data-q-send]').disabled = ro && !ctl;
  }
  function head() {
    const t = q('[data-q-title]'), b = q('[data-q-btw]');
    t.textContent = C ? C.title : 'New conversation';
    t.title = t.textContent;
    q('[data-q-more]').hidden = !(C && C.id);
    b.hidden = !(C && C.agent);
    if (C && C.agent) {  // data-kind on the wrapper: classic shell.css colours the text of any a[data-kind]
      const a = b.querySelector('a');
      a.textContent = 'btw · ' + C.agent;
      a.href = '/a/' + encodeURIComponent(C.agent);
      a.title = 'Open ' + C.agent;
      b.setAttribute('data-kind', kindOf(C.agent));
    }
    q('[data-q-input]').placeholder = C && C.agent ? 'Ask about ' + C.agent + ' (not sent to it)'
      : 'Message · Enter sends, Shift+Enter new line';
    pm7.$$('.q-it', q('[data-q-list]')).forEach((it) => {
      if (C && it.getAttribute('data-q-id') === String(C.id)) it.setAttribute('aria-current', 'true');
      else it.removeAttribute('aria-current');
    });
  }
  function apply(c) {
    C = c;
    head();
    chips();
  }
  function empty() {
    const e = el('div', 'q-empty'), s = el('div', 'q-sugg');
    e.append(icon('chat'), el('b', '', 'Ask anything'), el('span', '', 'One-shot claude -p, saved to history after ' +
      'the first answer. It never touches your agents or their panes.'));
    STARTERS.forEach((t) => {
      const b = el('button', 'x-btn sm', t.replace(/[\s:]+$/, '') + '…');
      b.type = 'button';
      b.setAttribute('data-q-start', t);
      s.appendChild(b);
    });
    e.appendChild(s);
    return e;
  }
  function bottom() { const l = q('[data-q-log]'); l.scrollTop = l.scrollHeight; }
  function userMsg(text) {
    const m = el('div', 'q-m u');
    m.appendChild(el('div', 't', text));
    return m;
  }
  function toolChip(t) {
    const dt = el('details', 'q-tool'), s = el('summary'), fam = /^[a-z]+$/.test(t.fam || '') ? t.fam : 'other';
    s.append(el('span', 'chip q-tc tool-' + fam, t.name === 'Agent' ? 'Sub-agent' : t.name || 'tool'),
      el('span', 'ell muted', t.line || ''));
    dt.appendChild(s);
    dt.setAttribute('data-q-tool', t.id || '');
    return dt;
  }
  function toolRes(dt, t) {
    if (!dt) return;
    dt.appendChild(el('pre', 'q-res', t.text || '(no output)'));
    dt.classList.toggle('bad', !!t.err);
  }
  function answer(model) {
    const m = el('div', 'q-m a'), h = el('div', 'h', model || 'default model'), tools = el('div', 'q-tools');
    m.append(h, tools, el('div', 't'));
    return m;
  }
  function finish(m, html, text) {
    const t = m.querySelector('.t');
    if (html) { t.classList.add('md'); t.innerHTML = html; } else if (text != null) t.textContent = text;
    m.setAttribute('data-text', t.innerText || t.textContent || '');
    const a = el('div', 'q-ma'), cp = el('button', 'x-btn sm');
    cp.type = 'button';
    cp.setAttribute('data-q-copy', '');
    cp.append(icon('copy'), 'Copy');
    a.appendChild(cp);
    m.appendChild(a);
  }
  function showMsgs(msgs) {
    const L = q('[data-q-log]');
    L.replaceChildren();
    if (!msgs.length) { L.appendChild(empty()); return; }
    msgs.forEach((x) => {
      if (x.role === 'user') { L.appendChild(userMsg(x.text)); return; }
      const m = answer(x.model), tl = m.querySelector('.q-tools');
      (x.tools || []).forEach((t) => { const c = toolChip(t); toolRes(c, t); tl.appendChild(c); });
      finish(m, x.html, x.text || '');  // html: the server's rendered, escaped markdown
      L.appendChild(m);
    });
    bottom();
  }
  async function openConv(id) {
    stop();
    let j = null;
    try { j = await getJSON('/qchat/convs/' + encodeURIComponent(id)); } catch (r) {
      pm7.fail('Opening the conversation', r || null);
      return;
    }
    apply(j.conv);
    showMsgs(j.messages || []);
    if (pm7.phone()) side(false);
  }
  function newConv() {
    stop();
    draft = fresh();
    apply(null);
    showMsgs([]);
    if (pm7.phone()) side(false);
  }

  /* ---------- send, stream, stop ---------- */
  function busy(on) {
    const b = q('[data-q-send]'), ta = q('[data-q-input]');
    b.classList.toggle('stop', on);
    b.setAttribute('aria-label', on ? 'Stop the answer' : 'Send');
    b.title = on ? 'Stop the answer' : 'Send (Enter)';
    b.replaceChildren(icon(on ? 'stop' : 'up'));
    b.disabled = false;
    ta.disabled = on || !!(C && C.archived);
    if (!on && isOpen() && !pm7.phone()) ta.focus();
  }
  function stop() { if (ctl) { ctl.abort(); ctl = null; } }
  function form(text) {
    const k = cfg(), f = new URLSearchParams({ text: text, model: k.model || '', profile: k.profile || 'none' });
    if (C && C.id) f.set('conv', String(C.id));
    if (k.profile !== 'none' && k.folder) f.set('folder', k.folder);
    if (k.profile !== 'none') (k.mcp || []).forEach((m) => f.append('mcp', m));
    if (k.profile !== 'none' || (opts && opts.skills_in_none)) (k.skills || []).forEach((s) => f.append('skills', s));
    return f;
  }
  async function send(raw) {
    const text = String(raw == null ? '' : raw).trim(), ta = q('[data-q-input]'), L = q('[data-q-log]');
    if (!text || ctl || (C && C.archived)) return false;
    const k = cfg(), body = form(text), m = answer(k.model), t = m.querySelector('.t');
    const tl = m.querySelector('.q-tools'), tools = {};
    const e = L.querySelector('.q-empty');
    if (e) e.remove();
    ta.value = '';
    L.append(userMsg(text), m);
    bottom();
    ctl = new AbortController();
    busy(true);
    let errText = '';
    const res = await pm7.qstream.send(body, (ev, o) => {
      if (ev === 'conv' && o.id != null) {
        if (!C) apply(Object.assign({ id: o.id, title: o.title || 'New chat', msgs: 0, agent: '', archived: false },
          draft, { mcp: draft.mcp.slice(), skills: draft.skills.slice() }));
        else if (!C.id) C.id = o.id;
        C.msgs = (C.msgs || 0) + 1;
        head();
        chips();
      } else if (ev === 'delta' && o.t) { t.textContent += o.t; bottom(); }
      else if (ev === 'tool') { tools[o.id] = toolChip(o); tl.appendChild(tools[o.id]); bottom(); }
      else if (ev === 'toolres') toolRes(tools[o.id], o);
      else if (ev === 'done') { finish(m, o.html, null); bottom(); }
      else if (ev === 'error') errText = 'Quick chat failed: ' + String(o.msg || 'error').slice(0, 160);
    }, { signal: ctl.signal });
    ctl = null;
    if (res.aborted) errText = 'Stopped.';
    else if (res.lost) errText = 'Connection lost.';
    else if (!res.ok) errText = await pm7.why(res.r, 'Quick chat failed');
    if (errText) L.appendChild(el('p', 'q-err', errText));
    if (!t.textContent && !t.childElementCount && !tl.childElementCount) m.remove();
    else if (!m.querySelector('.q-ma')) finish(m, null, null);
    if (C && res.done) C.msgs += 1;
    busy(false);
    bottom();
    refreshList();
    return res.done;
  }

  /* ---------- events (on the panel and its openers; no document keydown) ---------- */
  function onClick(e) {
    const t = e.target && e.target.closest ? e.target : null;
    if (!t) return;
    let b;
    if (t.closest('[data-q-close]')) close();
    else if (t.closest('[data-q-dock]')) {
      const on = !P().classList.contains('dock');
      dock(on);
      pm7.store.set('qchat.dock', on ? '1' : '0');
    }
    else if (t.closest('[data-q-side]')) {
      const on = q('[data-q-sidebar]').hidden;
      side(on);
      if (!pm7.phone()) pm7.store.set('qchat.side', on ? '1' : '0');
      if (on) q('[data-q-search]').focus();
    } else if (t.closest('[data-q-new]')) { newConv(); q('[data-q-input]').focus(); }
    else if ((b = t.closest('[data-q-id]'))) openConv(b.getAttribute('data-q-id'));
    else if ((b = t.closest('[data-q-start]'))) {
      const ta = q('[data-q-input]');
      ta.value = b.getAttribute('data-q-start');
      ta.focus();
      ta.setSelectionRange(ta.value.length, ta.value.length);
    } else if ((b = t.closest('[data-q-copy]'))) {
      const m = b.closest('.q-m'), txt = m ? m.getAttribute('data-text') || '' : '';
      const ok = () => pm7.toast('Copied'), no = () => pm7.toast('Copy failed', '', { error: true });
      if (navigator.clipboard) navigator.clipboard.writeText(txt).then(ok, no); else no();
    } else if ((b = t.closest('[data-q-btw] a')) && !e.metaKey && !e.ctrlKey && C && C.agent) {
      e.preventDefault();
      if (pm7.tabs) pm7.tabs.open('agent', C.agent, '/a/' + encodeURIComponent(C.agent));
    } else if ((b = t.closest('[data-q-send].stop'))) { e.preventDefault(); stop(); }
  }
  function wire() {
    const p = P();
    if (!p) return;
    p.addEventListener('click', onClick);
    q('[data-q-form]').addEventListener('submit', (e) => {
      e.preventDefault();
      if (!ctl) send(q('[data-q-input]').value);
    });
    q('[data-q-input]').addEventListener('keydown', (e) => {
      if (e.key !== 'Enter' || e.shiftKey || e.altKey || e.isComposing) return;
      if (pm7.phone() && !e.metaKey && !e.ctrlKey) return;  // phones: Enter is a new line, the button sends
      e.preventDefault();
      if (!ctl) send(e.target.value);
    });
    q('[data-q-search]').addEventListener('input', pm7.debounce((e) => {
      query = e.target.value.trim();
      refreshList();
    }, 200));
  }
  d.addEventListener('click', (e) => {
    const b = e.target && e.target.closest ? e.target.closest('[data-q-open]') : null;
    if (!b) return;
    e.preventDefault();
    toggle();
  });

  pm7.qchat = { open: open, close: close, toggle: toggle, isOpen: isOpen,
    /* for js/qchat-menus.js only (chip and ⋯ menus) */
    core: { cfg: cfg, conv: () => C, locked: locked, opts: () => opts || {}, chips: chips, head: head, q: q,
      refreshList: refreshList, newConv: newConv, setTitle: (t) => { if (C) C.title = t; head(); renderList(); },
      fresh: () => !C } };
  /* /?qchat=<id> (a core search hit, a link from elsewhere): open that conversation, then drop the parameter */
  function deepLink() {
    const u = new URL(window.location.href), id = u.searchParams.get('qchat');
    if (id == null) return;
    u.searchParams.delete('qchat');
    const done = () => { window.history.replaceState(window.history.state, '', u.pathname + u.search + u.hash); };
    if (/^\d+$/.test(id)) open(Number(id)).finally(done); else done();
  }
  pm7.ready(() => {
    if (!P()) return;
    wire();
    pm7.layers.soft({ rank: 30, open: isOpen, close: close, scrim: false });
    const row = pm7.keys && pm7.keys.table.find((t) => t.id === 'qchat');  // key Q and the > command "Quick chat"
    if (row) row.run = () => { toggle(); };
    deepLink();
  });
})();
