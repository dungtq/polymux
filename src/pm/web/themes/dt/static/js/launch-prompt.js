/* dt Launch prompt box (PRD.md §6.12 "prompt with templates, @ files, attachments"), wired by js/launch.js for each
   loaded form (dt _launch_form.html: textarea[data-l-prompt], [data-l-chips], [data-l-attach], input[data-l-file],
   [data-l-at] listbox, [data-l-pbox]). Templates are launch.js's.
   Globals: pm7.launch.prompt = {wire(form, base), paths(form) -> [saved path], uploading(form) -> bool,
     tokenAt(value, pos) -> {start, text} | null, split(text) -> {dir, q} (pure; tested)}.
   Attachments: the clip button (file picker), paste and drop (pm7.attach.wire on the box). Each file is uploaded at
   once with pm7.attach (js/attach.js: POST <base>/image, one .chip.att per file, .up while uploading, × removes it;
   a failure toasts its reason); launch.js adds the saved paths to the prompt when it launches and waits while one is
   still uploading.
   @ files: "@name" at the start or after a space lists the project directory's entries (GET <base>/atfiles?dir=&q=:
   names only, the server's containment rules); "@sub/" lists that folder. ↑ ↓ move, Enter or Tab picks, a click
   picks; the list closes when the token ends or the box loses focus. Esc belongs to the dialog (layers.js, DESIGN §7):
   it closes the dialog; the text comes back on the next open (launch.js). Listeners sit on the box, never on the
   document. */
(function () {
  'use strict';
  const pm7 = window.pm7, d = document;
  const MAX_AT = 12;

  /* ---------- pure helpers ---------- */
  function tokenAt(value, pos) {  // the @token that ends at pos: "@" starts the text or follows whitespace
    const before = String(value || '').slice(0, pos);
    const m = /(^|\s)@(\S*)$/.exec(before);
    return m ? { start: before.length - m[2].length - 1, text: m[2] } : null;
  }
  function split(text) {  // "a/b/c" -> {dir: "a/b/", q: "c"}
    const i = text.lastIndexOf('/');
    return i < 0 ? { dir: '', q: text } : { dir: text.slice(0, i + 1), q: text.slice(i + 1) };
  }

  /* ---------- attachments (pm7.attach, js/attach.js) ---------- */
  async function upload(form, base, file) {
    const A = pm7.attach, name = file.name || 'pasted file';
    const c = A.chip(form.querySelector('[data-l-chips]'), name);
    c.classList.add('up');
    c.title = 'Uploading ' + name + '…';
    try {
      const path = await A.upload(base, file);
      if (c.isConnected) A.done(c, path);
    } catch (e) {
      if (!c.isConnected) return;
      c.remove();
      pm7.toast('Could not attach ' + e.message, '', { error: true });
    }
  }
  function files(form, base, list) {
    Array.from(list || []).forEach((f) => { upload(form, base, f); });
  }
  function paths(form) { return pm7.attach.paths(form); }
  function uploading(form) { return pm7.attach.uploading(form); }

  /* ---------- @ files ---------- */
  function atClose(st) {
    st.seq++;
    st.tok = null;  // a pending (debounced) fetch then does nothing
    st.items = [];
    st.list.hidden = true;
    st.list.replaceChildren();
    st.ta.setAttribute('aria-expanded', 'false');
    st.ta.removeAttribute('aria-activedescendant');
  }
  function atInput(st) {
    const ta = st.ta, tok = ta.selectionStart === ta.selectionEnd ? tokenAt(ta.value, ta.selectionStart) : null;
    if (!tok) { atClose(st); return; }
    st.tok = tok;
    st.fetch();
  }
  async function atFetch(st) {
    const sp = split(st.tok.text), n = ++st.seq;
    const q = new URLSearchParams({ dir: sp.dir, q: sp.q });
    let j = null;
    try {
      const r = await pm7.fetch(st.base + '/atfiles?' + q.toString());
      j = r.ok ? await r.json() : null;
    } catch (e) { j = null; }
    if (n !== st.seq || d.activeElement !== st.ta) return;
    st.items = (j && Array.isArray(j.items) ? j.items : []).slice(0, MAX_AT)
      .map((it) => ({ name: String(it.name), dir: !!it.dir, path: sp.dir + String(it.name) }));
    st.active = 0;
    atRender(st);
  }
  function atRender(st) {
    if (!st.items.length) { atClose(st); return; }
    st.list.replaceChildren(...st.items.map((it, i) => {
      const o = d.createElement('div'), ic = pm7.icon(it.dir ? 'folder' : 'file'), t = d.createElement('span');
      o.className = 'l-ato';
      o.id = 'l-at-' + i;
      o.setAttribute('role', 'option');
      o.setAttribute('aria-selected', String(i === st.active));
      o.setAttribute('data-l-ati', String(i));
      t.className = 'ell';
      t.textContent = it.name + (it.dir ? '/' : '');
      if (ic) o.appendChild(ic);
      o.appendChild(t);
      return o;
    }));
    st.list.hidden = false;
    st.ta.setAttribute('aria-expanded', 'true');
    st.ta.setAttribute('aria-activedescendant', 'l-at-' + st.active);
  }
  function atMove(st, by) {
    st.active = (st.active + by + st.items.length) % st.items.length;
    st.list.querySelectorAll('[role=option]').forEach((o, i) => {
      o.setAttribute('aria-selected', String(i === st.active));
    });
    st.ta.setAttribute('aria-activedescendant', 'l-at-' + st.active);
    const o = st.list.children[st.active];
    if (o) o.scrollIntoView({ block: 'nearest' });
  }
  function atPick(st, i) {
    const it = st.items[i], ta = st.ta;
    if (!it || !st.tok) return;
    const ins = '@' + it.path + (it.dir ? '/' : ' '), start = st.tok.start, end = ta.selectionStart;
    ta.value = ta.value.slice(0, start) + ins + ta.value.slice(end);
    ta.setSelectionRange(start + ins.length, start + ins.length);
    atClose(st);
    ta.dispatchEvent(new Event('input', { bubbles: true }));  // a folder lists its entries next
  }

  /* ---------- wiring ---------- */
  function wire(form, base) {
    const ta = form.querySelector('[data-l-prompt]'), list = form.querySelector('[data-l-at]');
    const box = form.querySelector('[data-l-pbox]'), file = form.querySelector('[data-l-file]');
    if (!ta || !list || !box || !base) return;
    const st = { ta: ta, list: list, base: base, items: [], active: 0, tok: null, seq: 0 };
    st.fetch = pm7.debounce(() => { if (st.tok) atFetch(st); }, 120);
    list.id = 'l-at';
    ta.setAttribute('aria-autocomplete', 'list');
    ta.setAttribute('aria-controls', 'l-at');
    ta.setAttribute('aria-expanded', 'false');
    ta.addEventListener('input', () => atInput(st));
    ta.addEventListener('blur', () => { setTimeout(() => { if (d.activeElement !== ta) atClose(st); }, 0); });
    ta.addEventListener('keydown', (e) => {
      if (list.hidden || e.isComposing || e.metaKey || e.ctrlKey || e.altKey) return;
      if (e.key === 'ArrowDown' || e.key === 'ArrowUp') {
        e.preventDefault();
        atMove(st, e.key === 'ArrowDown' ? 1 : -1);
      } else if (e.key === 'Enter' || e.key === 'Tab') {
        e.preventDefault();
        e.stopPropagation();
        atPick(st, st.active);
      }
    });
    list.addEventListener('pointerdown', (e) => { e.preventDefault(); });  // keep the cursor in the box
    list.addEventListener('click', (e) => {
      const o = e.target.closest('[data-l-ati]');
      if (o) atPick(st, +o.getAttribute('data-l-ati'));
    });
    pm7.attach.wire(box, (fl) => { files(form, base, fl); });  // paste in the box, drop on it
    form.querySelector('[data-l-attach]').addEventListener('click', () => { file.click(); });
    file.addEventListener('change', () => { files(form, base, file.files); file.value = ''; });
  }

  pm7.launch = pm7.launch || {};
  pm7.launch.prompt = { wire: wire, paths: paths, uploading: uploading, tokenAt: tokenAt, split: split };
})();
