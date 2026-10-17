/* dt markdown reader (DESIGN.md §6.19, §7; user issue #250): dialog#x-md (shell/_mdview.html), a modal centred on
   the screen (full screen on phones) over any page. Today opened by aside.js for the .md links of the aside chat
   (classic chat-md.js in the aside's embed posts {pm: 'drawer', act: 'md', id, path}), and for a chat's .md links
   (classic chat-md.js a[data-md]) on a page without classic's docked reader #mdpane (Builder): there chat-md.js
   finds no pane and does nothing, so this file opens them here (capture phase, before chat-md.js).
   Globals: pm7.md = {open(agentId, path) -> bool, file(path) -> bool, current() -> path | null}.
     open: loads GET /a/<id>/md?path= (the server's rendered, escaped markdown: div.mdv[data-path] > div.md; a
     relative path is looked up in the agent's folder, its project, then the files its chat named) into
     [data-md-body] and shows the dialog (pm7.dialog: Esc, backdrop, focus trap and return).
     file: the same dialog for a file the Agents Library lists (a skill's SKILL.md, a rule, a script; the ⋯ View of a
     Library row, js/agents.js), from the core's read-only GET /agents/library/view?path= (same markup; a script is one
     code block); a link inside opens only another listed file.
   Inside the body: a relative .md link opens in the same dialog, against the shown file's folder (an absolute one
   as is; #frag scrolls to that heading); #frag alone scrolls to its heading (GitHub-style slug); other relative
   links stay (toast); http(s) / mailto links open a new tab (the server set target=_blank).
   Head: the real path ([data-md-title], cut from the left), A- / A+ ([data-md-zoom]: pm.v7.chat.md, the size the
   session reader uses, 10-32 px or auto -> --md-size on the dialog), Copy path ([data-md-copy]), Close
   [data-act=close] (keys.js -> pm7.layers.close). */
(function () {
  'use strict';
  const pm7 = window.pm7, d = document;
  const AGENT = /^[A-Za-z0-9][\w.:@-]{0,79}$/, SCHEME = /^[a-z][a-z0-9+.-]*:/i, MIN = 10, MAX = 32;
  const MISSING = 'Not found: pm looked in the agent\'s folder, its project, the files its chat named and the ' +
    'folders it worked in. Files in hidden folders never open.';
  const UNLISTED = 'Not shown: only files the Agents Library lists open here (no symlink out of them, no other ' +
    'hidden folder).';
  let agent = '', cur = '', seq = 0;  // agent '' = a Library file (pm7.md.file)
  const D = () => d.getElementById('x-md');
  const q = (sel) => { const x = D(); return x ? x.querySelector(sel) : null; };

  /* ---------- text size (shared with the session reader's A- / A+) ---------- */
  function size() {
    const v = parseInt(pm7.store.get('chat.md', '0'), 10);
    return v >= MIN && v <= MAX ? v : 0;
  }
  function applySize() {
    const x = D(), v = size(), val = q('[data-md-zoom-val]');
    if (!x) return;
    if (v) x.style.setProperty('--md-size', v + 'px'); else x.style.removeProperty('--md-size');
    if (val) val.textContent = v ? v + 'px' : 'auto';
  }
  function zoom(by) {
    const md = q('[data-md-body] .md') || q('[data-md-body]');
    const now = size() || Math.round(parseFloat(md ? getComputedStyle(md).fontSize : '') || 14);
    pm7.store.set('chat.md', String(Math.max(MIN, Math.min(MAX, now + by))));
    applySize();
  }

  /* ---------- paths ---------- */
  function dirOf(p) { const i = p.lastIndexOf('/'); return i < 0 ? '' : p.slice(0, i + 1); }
  function join(dir, rel) {  // "a/b/" + "../c.md" -> "a/c.md"; a leading "/" or "~" stays
    const out = [];
    (dir + rel).split('/').forEach((s, i) => {
      if (s === '..' && out.length && out[out.length - 1] !== '..' && out[out.length - 1] !== '') out.pop();
      else if (s !== '.' && (s !== '' || i === 0)) out.push(s);
    });
    return out.join('/');
  }
  function decode(s) { try { return decodeURIComponent(s); } catch (e) { return s; } }
  function slug(t) { return t.trim().toLowerCase().replace(/[^\p{L}\p{N}_\- ]+/gu, '').replace(/ /g, '-'); }
  function jump(frag) {
    const want = slug(decode(frag)), b = q('[data-md-body]');
    const h = b && Array.from(b.querySelectorAll('h1, h2, h3, h4, h5, h6')).find((x) => slug(x.textContent) === want);
    if (h) h.scrollIntoView({ block: 'start' });
  }

  /* ---------- load ---------- */
  function title(t) {
    const h = q('[data-md-title]');
    if (h) { h.textContent = t; h.parentNode.title = t; }
  }
  function note(text, err) {
    const b = q('[data-md-body]');
    if (b) b.replaceChildren(pm7.el('p.x-mdn' + (err ? '.err' : ''), null, text));
  }
  function load(path, frag) {
    const b = q('[data-md-body]'), n = ++seq;
    if (!b) return;
    cur = path;
    title(path);
    note('Loading…');
    const url = (agent ? '/a/' + encodeURIComponent(agent) + '/md' : '/agents/library/view') + '?path=' +
      encodeURIComponent(path);
    pm7.fetch(url, { headers: { Accept: 'text/html' } }).then(async (r) => {
      if (n !== seq) return;
      if (!r.ok) {
        note(r.status === 404 ? (agent ? MISSING : UNLISTED) : 'Could not load the file (' + r.status + ').', true);
        return;
      }
      const html = await r.text();
      if (n !== seq) return;
      b.innerHTML = html;  // server-rendered, escaped markdown (core markdown_render), as classic chat-md.js
      const v = b.querySelector('.mdv');
      cur = (v && v.getAttribute('data-path')) || path;
      title(cur);
      b.scrollTop = 0;
      if (frag) jump(frag);
    }, () => { if (n === seq) note('Could not load the file: no answer.', true); });
  }
  function closed() {  // a late answer is dropped; the next open starts empty
    const b = q('[data-md-body]');
    seq++;
    if (b) b.replaceChildren();
  }
  function open(id, path) {
    id = String(id == null ? '' : id);
    return AGENT.test(id) && show(id, path);
  }
  function show(id, path) {  // id '' = a Library file
    const x = D();
    if (!x || typeof path !== 'string' || !path || path.length > 4096) return false;
    agent = id;
    applySize();
    load(path);
    pm7.dialog(x, { focus: q('[data-md-body]'), onClose: closed });
    return true;
  }

  /* ---------- clicks inside the dialog ---------- */
  function link(a, e) {
    const h = a.getAttribute('href') || '';
    if (a.classList.contains('chat-img') || SCHEME.test(h) || h.indexOf('//') === 0) return;  // imgview / new tab
    e.preventDefault();
    const i = h.indexOf('#'), frag = i < 0 ? '' : h.slice(i + 1);
    const base = decode((i < 0 ? h : h.slice(0, i)).split('?')[0]);
    if (!base) { if (frag) jump(frag); return; }
    if (!/\.md$/i.test(base)) { pm7.toast('Only .md files open here'); return; }
    load(/^[/~]/.test(base) ? base : join(dirOf(cur), base), frag);
  }
  function copy() {
    const done = () => pm7.toast('Path copied'), fail = () => pm7.toast('Copy failed', '', { error: true });
    if (navigator.clipboard && cur) navigator.clipboard.writeText(cur).then(done, fail); else fail();
  }
  pm7.ready(() => {
    const x = D();
    if (!x) return;
    x.addEventListener('click', (e) => {
      const t = e.target;
      if (!t || !t.closest) return;
      const z = t.closest('[data-md-zoom]');
      if (z) { zoom(parseInt(z.getAttribute('data-md-zoom'), 10) || 0); return; }
      if (t.closest('[data-md-copy]')) { copy(); return; }
      const a = t.closest('[data-md-body] a[href]');
      if (a) link(a, e);
    });
  });

  /* a chat's .md link where classic has no docked reader (Builder): open it here */
  d.addEventListener('click', (e) => {
    const t = e.target, a = t && t.closest ? t.closest('a[data-md]') : null;
    if (!a || d.getElementById('mdpane') || (D() && D().contains(a))) return;
    const host = a.closest('[data-agent]') || d.querySelector('[data-agent]');
    if (!host || !open(host.getAttribute('data-agent'), a.getAttribute('data-md') || '')) return;
    e.preventDefault();
    e.stopPropagation();
  }, true);

  pm7.md = { open: open, file: (path) => /^~\//.test(String(path || '')) && show('', path),
    current: () => (D() && D().open && cur ? cur : null) };
})();
