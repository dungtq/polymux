/* dt theme (pm v7) layers: the ONE layer stack of DESIGN.md §7. Modal dialogs, menus, phone sheets, the toast, the
   scrim, the Esc order, the outside-click rule, focus return to the opener and the dialog focus trap.
   Globals (on window.pm7):
     pm7.dialog(el | id, {focus, opener, onClose(value, el)}) -> el   showModal() on a <dialog> (#x-pal, #x-keys, ...)
     pm7.confirm({title, body, ok, cancel, danger, typed, undo}) -> Promise<bool>   in #x-dlg; typed = exact name to
       type; undo = the sentence after "Type <name> to confirm." (default "This cannot be undone."; e.g. a backup)
     pm7.menu(anchor, items, {label, align, onClose}) -> el | null      #x-menu; phone (< 700 px): #x-sheet
       items: [{label, icon, run(e, host), href, danger, disabled, note, kbd, keep} | {sep: true}]; same anchor
       again = close; keep: the menu stays open after run (steppers: A+ / A-); run gets the menu host (labels)
     pm7.sheet(nodes, {label, head, role, anchor, onClose}) -> el      #x-sheet bottom sheet over #x-scrim
     pm7.toast(text, key, {error, ms})                                #x-toast, 1.4 s (error 4 s)
       key = the shortcut's label: a shortcut toast, shaped by Settings › Display › Shortcut helper (html[data-ktoast]
       from head.js): "action" = no key chip, "off" = no toast; toasts without a key are always shown
     pm7.layers = {top, close(el?), closeLight, anyOpen, soft({rank, open, close, scrim}), sync}
   Esc order: dialog -> menu / sheet -> account menu -> soft layers below rank 50 (overlay sidebar 40) -> clear
   filter text -> leave text field -> soft layers from rank 50 (aside 70). Other files register soft layers; they
   never add their own Esc, outside-click or backdrop handler (V7-UI-4).
   hx-confirm on shell markup (not inside main.x-cl) opens pm7.confirm (classic dialog.js keeps the page content's).
   Hooks: dialog#x-pal, dialog#x-keys, dialog#x-dlg (content built here), div#x-menu, div#x-sheet, div#x-scrim,
   div#x-toast[role=status] (shown with .on), details#x-acct. A menu, sheet or toast opened over a modal dialog is
   moved into that dialog while shown (the rest of the page is inert under showModal). */
(function () {
  'use strict';
  const pm7 = window.pm7, d = document;
  const stack = [];        // {el, kind: 'dialog' | 'menu' | 'sheet', anchor, opener, onClose}
  const softs = [];        // {rank, open(), close(), scrim()}
  const homes = new WeakMap();
  const RANK_FIELD = 50;
  const FIELD = 'input:not([type=hidden]):not([disabled]), select:not([disabled]), textarea:not([disabled])';
  const FOCUSABLE = 'a[href], button:not([disabled]), ' + FIELD + ', [tabindex]:not([tabindex="-1"])';
  const FILTER = 'input[data-sfilter], input[data-filter], [data-filter] input, input[type=search]';
  const M = 8, GAP = 4;    // viewport margin and anchor gap of a menu (px)

  const byId = (id) => d.getElementById(id);
  const el = pm7.el;  // core.js: el(tag, cls, text)
  function shown(n) { return !!(n.offsetWidth || n.offsetHeight || n.getClientRects().length); }
  function focusables(root) { return Array.from(root.querySelectorAll(FOCUSABLE)).filter(shown); }
  function focus(n) { if (n && n.focus) n.focus({ preventScroll: true }); }
  function find(n) { for (let i = stack.length - 1; i >= 0; i--) if (stack[i].el === n) return stack[i]; return null; }
  function owner(n) {
    for (let i = stack.length - 1; i >= 0; i--) if (stack[i].el.contains(n)) return stack[i];
    return null;
  }
  function top() {
    for (let i = stack.length - 1; i >= 0; i--) if (!d.contains(stack[i].el)) finish(stack[i], '');
    return stack[stack.length - 1] || null;
  }
  function topDialog() {
    for (let i = stack.length - 1; i >= 0; i--) if (stack[i].kind === 'dialog') return stack[i].el;
    return null;
  }

  /* a light layer (menu, sheet, toast) shown over a modal dialog lives inside it; back home afterwards */
  function park(n) {
    if (!homes.has(n)) homes.set(n, n.parentNode);
    const dl = topDialog(), want = dl && dl !== n ? dl : homes.get(n);
    if (want && n.parentNode !== want) want.appendChild(n);
  }
  function unpark(dl) {
    ['x-menu', 'x-sheet', 'x-toast'].forEach((id) => {
      const n = byId(id), home = n && homes.get(n);
      if (n && home && dl.contains(n)) home.appendChild(n);
    });
  }

  function back(L) {  // focus returns to the opener when it was inside the layer (or nowhere)
    const a = d.activeElement;
    if (!L.opener || !d.contains(L.opener)) return;
    if (!a || a === d.body || L.el.contains(a) || !d.contains(a)) focus(L.opener);
  }
  function hideLight(L) {
    L.el.hidden = true;
    L.el.replaceChildren();
    ['position', 'left', 'top'].forEach((p) => L.el.style.removeProperty(p));
    if (L.anchor) L.anchor.setAttribute('aria-expanded', 'false');
  }

  /* remove L (and every layer above it) from the stack and run its close effects once */
  function finish(L, value) {
    const i = stack.indexOf(L);
    if (i < 0) return;
    while (stack.length > i + 1) finish(stack[stack.length - 1], '');
    stack.splice(i, 1);
    if (L.kind === 'dialog') {
      if (L.el.open) {
        if (L.el.close) L.el.close(value == null ? '' : String(value)); else L.el.removeAttribute('open');
      }
      unpark(L.el);
    } else {
      hideLight(L);
    }
    back(L);
    sync();
    if (L.onClose) L.onClose(L.kind === 'dialog' ? L.el.returnValue : value, L.el);
  }

  /* ---------- dialogs ---------- */
  const wired = new WeakSet();
  function wire(dl) {
    if (wired.has(dl)) return;
    wired.add(dl);
    dl.addEventListener('close', () => { const L = find(dl); if (L) finish(L, dl.returnValue); });
  }
  function focusIn(dl, f) {
    let c = f && shown(f) ? f : dl.querySelector('[autofocus]');
    if (!c || !shown(c)) c = Array.from(dl.querySelectorAll(FIELD)).filter(shown)[0];
    if (!c) c = focusables(dl)[0];
    focus(c);
  }
  function dialog(x, opts) {
    const dl = typeof x === 'string' ? byId(x) : x, o = opts || {};
    if (!dl) return null;
    wire(dl);
    if (find(dl)) { focusIn(dl, o.focus); return dl; }
    closeLight();
    stack.push({ el: dl, kind: 'dialog', anchor: null, opener: o.opener || d.activeElement, onClose: o.onClose });
    dl.returnValue = '';
    if (!dl.open) { if (dl.showModal) dl.showModal(); else dl.setAttribute('open', ''); }
    focusIn(dl, o.focus);
    sync();
    return dl;
  }

  /* a plain yes/no question (reversible) or a typed one (irreversible: OK stays disabled until the name matches) */
  function ask(opts) {
    const o = opts || {}, host = byId('x-dlg');
    if (!host) return Promise.resolve(false);
    const prev = find(host);
    if (prev) finish(prev, '');
    return new Promise((resolve) => {
      const typed = o.typed != null ? String(o.typed) : '';
      const title = el('h2', '', o.title || (typed ? 'Type the name to confirm' : 'Are you sure?'));
      title.id = 'x-dlg-t';
      const body = el('div', 'bd');
      if (o.body != null) body.appendChild(o.body.nodeType ? o.body : el('p', '', String(o.body)));
      const ok = el('button', 'x-btn ' + (o.danger || typed ? 'red' : 'pri'), o.ok || 'OK');
      const cancel = el('button', 'x-btn', o.cancel || 'Cancel');
      ok.type = cancel.type = 'button';
      const parts = [title, body];
      let input = null, answer = false;
      const done = (v) => { answer = v; const L = find(host); if (L) finish(L, v ? 'ok' : ''); };
      if (typed) {
        const hint = el('p', 'x-dlg-hint');
        hint.append('Type ', el('b', 'mono', typed), ' to confirm. ' + (o.undo || 'This cannot be undone.'));
        body.appendChild(hint);
        input = el('input', 'mono');
        input.type = 'text';
        input.autocomplete = 'off';
        input.spellcheck = false;
        input.setAttribute('aria-label', 'Type ' + typed + ' to confirm');
        ok.disabled = true;
        input.addEventListener('input', () => { ok.disabled = input.value !== typed; });
        input.addEventListener('keydown', (e) => {
          if (e.key !== 'Enter' || e.isComposing) return;
          e.preventDefault();
          if (input.value === typed) done(true);
        });
        parts.push(input);
      }
      const ft = el('div', 'ft');
      ft.append(cancel, ok);
      parts.push(ft);
      host.replaceChildren(...parts);
      host.setAttribute('aria-labelledby', title.id);
      ok.addEventListener('click', () => { if (!typed || input.value === typed) done(true); });
      cancel.addEventListener('click', () => done(false));
      dialog(host, { opener: o.opener, focus: input || (o.danger ? cancel : ok), onClose: () => resolve(answer) });
    });
  }

  /* ---------- menus and sheets ---------- */
  function item(it) {
    if (it.sep) return el('hr');
    const b = el(it.href ? 'a' : 'button', it.danger ? 'red' : '');
    if (it.href) b.href = it.href; else b.type = 'button';
    b.setAttribute('role', 'menuitem');
    if (it.disabled) { if (it.href) b.setAttribute('aria-disabled', 'true'); else b.disabled = true; }
    const ic = it.icon ? pm7.icon(it.icon) : null;
    if (ic) b.appendChild(ic);
    b.appendChild(el('span', '', it.label));
    if (it.note) b.appendChild(el('small', '', it.note));
    if (it.kbd) b.appendChild(el('kbd', '', it.kbd));
    b.addEventListener('click', (e) => {
      // the element's state, not it.disabled: a keep item's run may enable or disable another item
      if (b.disabled || b.getAttribute('aria-disabled') === 'true') { e.preventDefault(); return; }
      if (!it.href) e.preventDefault();
      const L = owner(b);
      if (L && !it.keep) finish(L, '');
      if (it.run) it.run(e, L ? L.el : null);
    });
    return b;
  }
  function place(host, anchor, align) {
    const s = host.style;
    s.setProperty('position', 'fixed');
    s.setProperty('left', '0px');
    s.setProperty('top', '0px');
    if (!anchor) return;
    const r = anchor.getBoundingClientRect(), w = host.offsetWidth, h = host.offsetHeight;
    const vw = d.documentElement.clientWidth, vh = window.innerHeight;
    const x = align === 'start' ? r.left : r.right - w;
    let y = r.bottom + GAP;
    if (y + h > vh - M && r.top - GAP - h >= M) y = r.top - GAP - h;
    s.setProperty('left', Math.round(Math.max(M, Math.min(x, vw - M - w))) + 'px');
    s.setProperty('top', Math.round(Math.max(M, Math.min(y, vh - M - h))) + 'px');
  }
  function menu(anchor, items, opts) {
    const o = opts || {};
    const open = anchor ? stack.find((L) => L.kind !== 'dialog' && L.anchor === anchor) : null;
    if (open) { finish(open, ''); return null; }
    const nodes = (items || []).map(item);
    if (pm7.phone()) return sheet(nodes, { label: o.label, role: 'menu', anchor: anchor, onClose: o.onClose });
    const host = byId('x-menu');
    if (!host) return null;
    closeLight();
    host.replaceChildren(...nodes);
    host.setAttribute('role', 'menu');
    if (o.label) host.setAttribute('aria-label', o.label);
    park(host);
    host.hidden = false;
    stack.push({ el: host, kind: 'menu', anchor: anchor || null, opener: anchor || d.activeElement,
      onClose: o.onClose, align: o.align });
    if (anchor) { anchor.setAttribute('aria-expanded', 'true'); anchor.setAttribute('aria-controls', 'x-menu'); }
    place(host, anchor, o.align);
    focus(focusables(host)[0]);
    return host;
  }
  function sheet(nodes, opts) {
    const o = opts || {}, host = byId('x-sheet');
    if (!host) return null;
    closeLight();
    const grab = el('div', 'grab');
    grab.setAttribute('aria-hidden', 'true');
    const ls = el('div', 'ls');
    if (o.role) ls.setAttribute('role', o.role);
    ls.append(...[].concat(nodes || []));
    host.replaceChildren(...[grab, o.head, ls].filter(Boolean));
    if (o.label) host.setAttribute('aria-label', o.label);
    if (!host.hasAttribute('tabindex')) host.setAttribute('tabindex', '-1');
    swipe(host);
    park(host);
    host.hidden = false;
    stack.push({ el: host, kind: 'sheet', anchor: o.anchor || null, opener: o.anchor || d.activeElement,
      onClose: o.onClose });
    if (o.anchor) o.anchor.setAttribute('aria-expanded', 'true');
    sync();
    focus(o.role === 'menu' ? focusables(ls)[0] || host : host);
    return host;
  }
  const swiped = new WeakSet();
  function swipe(host) {  // swipe down on a sheet closes it
    if (swiped.has(host)) return;
    swiped.add(host);
    let y0 = null;
    host.addEventListener('touchstart', (e) => { y0 = host.scrollTop <= 0 ? e.touches[0].clientY : null; },
      { passive: true });
    host.addEventListener('touchend', (e) => {
      if (y0 === null) return;
      const dy = e.changedTouches[0].clientY - y0;
      y0 = null;
      const L = find(host);
      if (L && dy > 60) finish(L, '');
    });
  }
  function closeAcct() {
    const a = byId('x-acct');
    if (!a || !a.open) return false;
    a.open = false;
    return true;
  }
  /* close every menu and sheet (never a dialog) and the account menu; true when something closed */
  function closeLight() {
    let any = closeAcct();
    for (let i = stack.length - 1; i >= 0; i--) {
      if (stack[i] && stack[i].kind !== 'dialog') { finish(stack[i], ''); any = true; }
    }
    return any;
  }

  /* ---------- toast ---------- */
  let toastT = 0;
  function toast(text, key, opts) {
    const t = byId('x-toast'), o = opts || {}, kt = d.documentElement.getAttribute('data-ktoast');
    if (!t || (key && kt === 'off')) return;
    if (kt === 'action') key = '';
    const parts = [];
    if (key) parts.push(el('kbd', '', key));
    parts.push(el('span', '', text));
    t.replaceChildren(...parts);
    t.classList.toggle('err', !!o.error);
    park(t);
    t.hidden = false;
    t.classList.add('on');
    clearTimeout(toastT);
    toastT = setTimeout(() => { t.classList.remove('on'); }, o.ms || (o.error ? 4000 : 1400));
  }

  /* ---------- soft layers, scrim ---------- */
  function soft(def) {
    softs.push(def);
    softs.sort((a, b) => a.rank - b.rank);
    sync();
  }
  function scrimSoft(S) { return typeof S.scrim === 'function' ? !!S.scrim() : !!S.scrim; }
  function sync() {
    const s = byId('x-scrim');
    if (!s) return;
    s.hidden = !(stack.some((L) => L.kind === 'sheet') || softs.some((S) => S.open() && scrimSoft(S)));
  }
  function runSoft(test) {  // the lowest rank first: it is the one nearest the top (Quick chat before sidebar)
    for (const S of softs) {
      if (test(S) && S.open()) { S.close(); sync(); return true; }
    }
    return false;
  }
  function anyOpen() { return !!top() || !!(byId('x-acct') || {}).open || softs.some((S) => S.open()); }
  function close(n, value) {
    const L = n ? find(n) || owner(n) : top();
    if (L) finish(L, value);
    else if (n && n.closest && n.closest('details[open]')) n.closest('details[open]').open = false;
  }

  /* ---------- keys: Esc (one listener, DESIGN §7 order), Tab trap, arrows in menus ---------- */
  function foreign() {  // an open <dialog> that is not ours (classic content): leave its Esc alone
    return Array.from(d.querySelectorAll('dialog[open]')).some((n) => !find(n));
  }
  function escape() {
    const L = top();
    if (L) { finish(L, ''); return true; }
    if (closeAcct()) { focus(byId('x-acct').querySelector('summary')); return true; }
    if (runSoft((S) => S.rank < RANK_FIELD)) return true;
    const a = d.activeElement;
    if (a && a.matches && a.matches(FILTER) && a.value) {
      a.value = '';
      a.dispatchEvent(new Event('input', { bubbles: true }));
      return true;
    }
    if (pm7.isField(a)) { a.blur(); return true; }
    return runSoft((S) => S.rank >= RANK_FIELD);
  }
  function trap(e, L) {
    const f = focusables(L.el);
    if (!f.length) { e.preventDefault(); return; }
    const i = f.indexOf(d.activeElement);
    if (e.shiftKey && i <= 0) { e.preventDefault(); focus(f[f.length - 1]); }
    else if (!e.shiftKey && (i < 0 || i === f.length - 1)) { e.preventDefault(); focus(f[0]); }
  }
  function rove(e, L) {
    const list = Array.from(L.el.querySelectorAll('[role=menuitem]')).filter((n) => shown(n) && !n.disabled);
    if (!list.length) return;
    e.preventDefault();
    const i = list.indexOf(d.activeElement), n = list.length, k = e.key;
    const j = k === 'Home' ? 0 : k === 'End' ? n - 1 : k === 'ArrowDown' ? (i + 1) % n : (i <= 0 ? n - 1 : i - 1);
    focus(list[j]);
  }
  d.addEventListener('keydown', (e) => {
    if (e.defaultPrevented || e.isComposing) return;
    if (e.key === 'Escape') {
      if (!stack.length && foreign()) return;
      if (escape()) { e.preventDefault(); e.stopPropagation(); }
      return;
    }
    const L = stack.length ? top() : null;
    if (!L) return;
    if (e.key === 'Tab' && L.kind === 'dialog') trap(e, L);
    else if (/^(ArrowDown|ArrowUp|Home|End)$/.test(e.key) && L.kind !== 'dialog' && !pm7.isField(e.target)) rove(e, L);
  }, true);

  /* ---------- the one outside-click rule ---------- */
  let downOut = null;
  function outside(e, n) {
    const r = n.getBoundingClientRect();
    return e.clientX < r.left || e.clientX > r.right || e.clientY < r.top || e.clientY > r.bottom;
  }
  d.addEventListener('pointerdown', (e) => {  // a backdrop click must start outside the box too (text selection)
    const t = e.target;
    downOut = t && t.tagName === 'DIALOG' && find(t) && outside(e, t) ? t : null;
  }, true);
  d.addEventListener('click', (e) => {
    const t = e.target;
    if (!t || t.nodeType !== 1) return;
    if (t.tagName === 'DIALOG' && t === downOut && find(t) && outside(e, t)) { finish(find(t), ''); return; }
    if (t === byId('x-scrim')) {
      if (!closeLight()) runSoft((S) => scrimSoft(S));
      e.preventDefault();
      return;
    }
    for (let i = stack.length - 1; i >= 0; i--) {
      const L = stack[i];
      if (L.kind === 'dialog' || L.el.contains(t) || (L.anchor && L.anchor.contains(t))) break;
      finish(L, '');
    }
    const acct = byId('x-acct');
    if (acct && acct.open && !acct.contains(t)) acct.open = false;
  }, true);

  /* keep a menu on its anchor; a menu whose anchor left the page (htmx swap) closes */
  let queued = false;
  function replace() {
    queued = false;
    stack.filter((L) => L.kind === 'menu').forEach((L) => {
      if (L.anchor && !d.contains(L.anchor)) finish(L, ''); else place(L.el, L.anchor, L.align);
    });
  }
  function later() {
    if (queued || !stack.some((L) => L.kind === 'menu')) return;
    queued = true;
    requestAnimationFrame(replace);
  }
  window.addEventListener('resize', () => { later(); sync(); });
  d.addEventListener('scroll', later, { capture: true, passive: true });

  /* hx-confirm="..." on dt shell markup (outside main.x-cl) asks with pm7.confirm, never the native popup.
     Capture phase on document and stopped there, so classic dialog.js (bubble phase on document) never handles the
     same event; inside main.x-cl (page content) the event is left to classic dialog.js. */
  d.addEventListener('htmx:confirm', (e) => {
    const q = e.detail && e.detail.question, elt = e.detail && e.detail.elt;
    if (!q || !elt || !elt.closest || elt.closest('main.x-cl')) return;
    e.preventDefault();
    e.stopImmediatePropagation();
    const b = elt.matches('form') ? elt.querySelector('button[type=submit], button:not([type])') : elt;
    const label = b ? b.textContent.trim() : '';
    pm7.confirm({ title: q, ok: label || 'OK', danger: !!(b && b.classList.contains('danger')) })
      .then((ok) => { if (ok) e.detail.issueRequest(true); });
  }, true);

  pm7.dialog = dialog;
  pm7.confirm = ask;
  pm7.menu = menu;
  pm7.sheet = sheet;
  pm7.toast = toast;
  pm7.layers = { top: top, close: close, closeLight: closeLight, anyOpen: anyOpen, soft: soft, sync: sync };
})();
