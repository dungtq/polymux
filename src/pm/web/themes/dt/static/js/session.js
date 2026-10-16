/* dt session tab frame (PRD §6.3, templates/session/_frame.html). README A5: the chat items, composer, reader and the
   Pane / Sub-agents / Terminal contents keep their classic JS (shell/_classic.html, flag classic_page); this file only
   runs the frame around them:
   - sub-tabs: the shared flat tabs (parts.js, event pm7:tab) drive the classic radios #tab-chat / #tab-pane /
     #tab-sub / #tab-term (name agent-tab) that classic chat.js, chatlog.js and composer.js (Keys button) read, and
     follow them back (composer Keys, chat.js restoring its last tab); ?st=<key> picks the first tab (server) and
     is kept in the URL (history.replaceState); Builder's phone Chat | Preview labels get aria-selected (the
     classic radios behind them take the keyboard);
   - state dot + word follow pm:agent_state (dt live.js);
   - Aa: chat text size menu (pm.v7.chat.size, 10-32px or auto -> --chat-size on .c-sess, which classic .chat-text
     reads); the reader's A- / A+ (data-md-zoom) -> pm.v7.chat.md -> --md-size;
   - Jump to top / bottom of the chat column (.c-chat[data-ci-scroll]); a chat ([data-ci-scroll], Builder's too)
     scrolled to its end stays there when it changes height (ResizeObserver: the phone composer showing / folding /
     growing, the window);
   - phone composer (LAYOUT §6, css/session.css): collapsed unless Settings › Display › Composer on phones is open
     (html.cmp-open, head.js); [data-c-cmp] shows / hides it (.c-sess.cmp-on / .cmp-off, aria-expanded, title,
     aria-controls = the .c-comp box, given id c-comp here); the cursor going in (⌘L in aside.js, Tab, btw's Use in
     composer) or a draft at load shows it; html.cmp-open changing later (Settings, a prefs refresh: one attribute
     observer on <html>) re-syncs the button. Not in embed;
   - tmux link -> a terminal tab (pm7.tabs.open), move to the aside (pm7.aside.fromTab), ⋯ events c:continue
     (the last reply's classic Continue… button), c:usage (opens the token line's details), c:btw (pm7.aside.btw;
     in the aside's iframe the parent shell's aside);
   - btw from the composer (README A9; classic qchat-aside.js is not loaded in dt): its btw icon ([data-aside]: the
     box's text is the question, then cleared) and `/btw <question>` (capture-phase submit, before htmx's own: it
     never reaches the agent).
   Hooks: section.c-sess[data-agent][data-c-mode], .c-tabs (p-tabs), input.c-radio, [data-c-dot], #agent-state,
   [data-c-font], [data-c-jump], [data-c-cmp], .c-comp, [data-c-term], [data-c-toaside]. Storage keys pm.v7.chat.*. */
(function () {
  'use strict';
  const pm7 = window.pm7, d = document;
  const STATES = ['running', 'waiting', 'failed', 'idle', 'unknown', 'ended'];
  const sess = () => d.querySelector('.c-sess');
  const aid = () => { const s = sess(); return s ? s.getAttribute('data-agent') : ''; };
  const toast = (t, err) => pm7.toast && pm7.toast(t, '', err ? { error: true } : undefined);
  const BTW = /^\/btw(?:\s+([\s\S]*))?$/i;

  /* ---------- sub-tabs <-> classic radios ---------- */
  const tabBtn = (key) => d.querySelector('.c-hd .c-tabs [data-p-tab="' + key + '"]');
  function setUrl(key) {
    if (!window.history || !history.replaceState) return;
    const u = new URL(window.location.href);
    if (key === 'chat') u.searchParams.delete('st'); else u.searchParams.set('st', key);
    if (u.href !== window.location.href) history.replaceState(history.state, '', u.href);
  }
  function toRadio(key) {
    const r = d.getElementById('tab-' + key);
    if (!r || r.checked) return;
    r.checked = true;
    r.dispatchEvent(new Event('change', { bubbles: true }));
  }
  function fromRadio(r) {
    const b = r && r.checked ? tabBtn(r.id.slice(4)) : null;
    if (b && b.getAttribute('aria-selected') !== 'true') b.click();  // parts.js selects it -> pm7:tab
  }
  d.addEventListener('pm7:tab', (e) => {
    const l = e.target;
    if (!l || !l.closest || !l.closest('.c-hd') || !e.detail) return;
    toRadio(e.detail.key);
    setUrl(e.detail.key);
  });
  d.addEventListener('change', (e) => {
    const t = e.target;
    if (!t || t.type !== 'radio') return;
    if (t.name === 'agent-tab') fromRadio(t);
    else if (t.name === 'bt') bldTabs();
  });
  function bldTabs() {
    pm7.$$('.bld-tabs label[for]').forEach((l) => {
      const r = d.getElementById(l.getAttribute('for')), on = !!(r && r.checked);
      l.setAttribute('aria-selected', on ? 'true' : 'false');
    });
  }
  function initTabs() {
    const want = new URL(window.location.href).searchParams.get('st');
    const b = want ? tabBtn(want) : null;
    if (b) { if (b.getAttribute('aria-selected') !== 'true') b.click(); toRadio(want); }
    else fromRadio(d.querySelector('input.c-radio:checked'));
    bldTabs();
  }

  /* ---------- live state ---------- */
  d.addEventListener('pm:agent_state', (e) => {
    const x = e.detail, id = aid();
    if (!x || !id || x.id !== id || STATES.indexOf(x.state) < 0) return;
    const dot = d.querySelector('.c-hd [data-c-dot]');
    if (dot) { STATES.forEach((s) => dot.classList.remove(s)); dot.classList.add(x.state); dot.title = x.state; }
    const w = d.getElementById('agent-state');
    const badge = w && w.querySelector('.badge');
    if (badge && badge.textContent !== x.state) {
      badge.className = 'badge st-' + x.state;
      badge.textContent = x.state;
    }
  });

  /* ---------- text sizes: chat (Aa) and reader (A- / A+) ---------- */
  const MIN = 10, MAX = 32;
  function size(name) {
    const v = parseInt(pm7.store.get('chat.' + name, '0'), 10);
    return v >= MIN && v <= MAX ? v : 0;
  }
  function applySizes() {
    const s = sess();
    if (!s) return;
    const c = size('size'), m = size('md');
    if (c) s.style.setProperty('--chat-size', c + 'px'); else s.style.removeProperty('--chat-size');
    if (m) s.style.setProperty('--md-size', m + 'px'); else s.style.removeProperty('--md-size');
    pm7.$$('[data-md-zoom-val]').forEach((v) => { v.textContent = m ? m + 'px' : 'auto'; });
  }
  function base(sel) {
    const ref = d.querySelector(sel);
    return Math.round(parseFloat(ref ? getComputedStyle(ref).fontSize : '') || 15);
  }
  function step(name, by, sel) {
    let v = size(name);
    if (by === 0) v = 0;
    else v = Math.max(MIN, Math.min(MAX, (v || base(sel)) + by));
    pm7.store.set('chat.' + name, String(v));
    applySizes();
    return v;
  }
  function fontMenu(btn) {
    const cur = size('size');
    pm7.menu(btn, [
      { label: 'Larger text', icon: 'plus', run: () => step('size', 1, '.c-msgs .chat-text') },
      { label: 'Smaller text', run: () => step('size', -1, '.c-msgs .chat-text') },
      { label: 'Reset' + (cur ? ' (now ' + cur + 'px)' : ' (auto)'), icon: 'refresh', disabled: !cur,
        run: () => step('size', 0) }
    ], { label: 'Chat text size' });
  }
  window.addEventListener('storage', (e) => { if (e.key && e.key.indexOf('pm.v7.chat.') === 0) applySizes(); });

  /* ---------- jump to top / bottom of the chat column ---------- */
  let raf = 0;
  function syncJump() {
    raf = 0;
    const sc = d.querySelector('.c-chat[data-ci-scroll]');
    if (!sc) return;
    const top = d.querySelector('[data-c-jump=top]'), bot = d.querySelector('[data-c-jump=bottom]');
    const far = sc.clientHeight * 1.5, shown = sc.getClientRects().length > 0;
    if (top) top.hidden = !shown || sc.scrollTop < far;
    if (bot) bot.hidden = !shown || sc.scrollHeight - sc.scrollTop - sc.clientHeight < far;
  }
  const later = () => { if (!raf) raf = window.requestAnimationFrame(syncJump); };
  /* at the end = within 8px of the bottom; noted on every scroll, so a shrink (which does not scroll) still knows */
  const atEnd = (sc) => sc.scrollHeight - sc.clientHeight - sc.scrollTop < 8;
  let end = true;
  d.addEventListener('scroll', (e) => {
    const t = e.target;
    if (!t || !t.matches || !t.matches('.c-sess [data-ci-scroll]')) return;
    end = atEnd(t);
    later();
  }, { capture: true, passive: true });
  d.addEventListener('htmx:afterSettle', later);
  window.addEventListener('resize', later);
  function pinInit() {  // after layout, before paint: no frame shows the newest lines hidden
    const sc = d.querySelector('.c-sess [data-ci-scroll]');  // .c-chat, Builder's .bld-msgs
    if (!sc || !window.ResizeObserver) return;
    let h = -1;
    new ResizeObserver(() => {
      const was = h;
      h = sc.clientHeight;
      if (was < 0) end = atEnd(sc);
      else if (h && h !== was && end) sc.scrollTop = sc.scrollHeight;  // h 0: the Chat panel is hidden
      later();
    }).observe(sc);
  }

  /* ---------- phone composer: collapsed until shown (css/session.css) ---------- */
  const cmpBox = () => d.querySelector('.c-sess:not(.embed) .c-comp');
  const root = d.documentElement;
  function cmpShown() {
    const s = sess();
    if (!s || s.classList.contains('cmp-on')) return true;
    return !s.classList.contains('cmp-off') && root.classList.contains('cmp-open');
  }
  function cmpSync() {
    const on = cmpShown(), t = on ? 'Hide composer' : 'Show composer', box = cmpBox();
    pm7.$$('.c-hd [data-c-cmp]').forEach((b) => {
      if (box) b.setAttribute('aria-controls', box.id); else b.removeAttribute('aria-controls');
      b.setAttribute('aria-expanded', on ? 'true' : 'false');
      b.setAttribute('aria-label', t);
      b.title = t;
    });
  }
  function cmpSet(on) {
    const s = sess();
    if (!s || s.classList.contains('embed')) return;
    s.classList.toggle('cmp-on', on);
    s.classList.toggle('cmp-off', !on);
    cmpSync();
  }
  function cmpToggle(btn) {
    const on = !cmpShown(), box = cmpBox(), ta = box && box.querySelector('[data-cmp-input]');
    if (!on && box && box.contains(d.activeElement)) btn.focus({ preventScroll: true });  // leave before it folds
    cmpSet(on);
    if (on && ta && !ta.disabled && pm7.phone()) ta.focus();  // shown to write in it
  }
  d.addEventListener('focusin', (e) => {
    const box = cmpBox();
    if (box && box.contains(e.target) && !cmpShown()) cmpSet(true);
  });
  d.addEventListener('input', (e) => {
    const box = cmpBox(), t = e.target;
    if (box && box.contains(t) && t.value && !cmpShown()) cmpSet(true);
  });
  function cmpInit() {  // a draft restored by classic composer.js (its DOMContentLoaded scan runs first)
    const box = cmpBox(), ta = box && box.querySelector('[data-cmp-input]');
    if (box && !box.id) box.id = 'c-comp';  // the box is the page's (agent / builder / ended .html), one per page
    if (ta && ta.value.trim()) cmpSet(true); else cmpSync();
  }
  d.addEventListener('pm7:look', () => { if (cmpBox()) cmpSync(); });  // head.js applied prefs (Composer on phones)

  /* ---------- clicks ---------- */
  d.addEventListener('click', (e) => {
    const t = e.target;
    if (!t || !t.closest || !t.closest('.c-sess')) return;
    const f = t.closest('[data-c-font]');
    if (f) { fontMenu(f); return; }
    const z = t.closest('[data-md-zoom]');
    if (z) { step('md', parseInt(z.getAttribute('data-md-zoom'), 10) || 0, '#mdpane .mdpane-b .md, #mdpane'); return; }
    const j = t.closest('[data-c-jump]');
    if (j) {
      const sc = d.querySelector('.c-chat[data-ci-scroll]');
      if (sc) sc.scrollTop = j.getAttribute('data-c-jump') === 'top' ? 0 : sc.scrollHeight;
      const cs = window.pm && window.pm.chatScroll;  // classic chatlog.js: follow new items again
      if (j.getAttribute('data-c-jump') === 'bottom' && cs) cs.bottom();
      later();
      return;
    }
    const term = t.closest('a[data-c-term]');
    if (term && pm7.tabs && !e.metaKey && !e.ctrlKey && !e.shiftKey && e.button === 0) {
      e.preventDefault();
      pm7.tabs.open('term', term.getAttribute('data-c-term'), term.getAttribute('href'));
      return;
    }
    if (t.closest('[data-c-toaside]')) { if (pm7.aside) pm7.aside.fromTab(); return; }
    const cb = t.closest('[data-c-cmp]');
    if (cb) { cmpToggle(cb); return; }
    const b = t.closest('[data-composer] [data-aside]');
    if (b) {
      e.preventDefault();
      e.stopPropagation();
      const ta = b.closest('[data-composer]').querySelector('[data-cmp-input]'), q = ta ? ta.value.trim() : '';
      if (btw(q) && q) clear(ta);
    }
  }, true);
  /* the shell's aside: this page's, or the parent shell's from inside the aside's iframe (embed, same origin) */
  function shellAside() {
    if (pm7.aside) return pm7.aside;
    try { return window.parent !== window && window.parent.pm7 ? window.parent.pm7.aside || null : null; }
    catch (err) { return null; }
  }
  function btw(question) {
    const a = shellAside();
    if (a) return a.btw(aid(), question || '');
    toast('btw opens in the chat aside', true);
    return false;
  }
  function clear(ta) { ta.value = ''; ta.dispatchEvent(new Event('input', { bubbles: true })); }
  d.addEventListener('submit', (e) => {
    const f = e.target;
    const live = '.c-sess :is([data-composer=live],[data-composer=builder]) [data-cmp-form]';
    if (!f || !f.matches || !f.matches(live)) return;
    const ta = f.querySelector('[data-cmp-input]'), m = ta && BTW.exec(ta.value.trim());
    if (!m) return;
    e.preventDefault();
    e.stopImmediatePropagation();
    if (btw((m[1] || '').trim())) clear(ta);
  }, true);

  /* ---------- ⋯ menu events (session/_frame.html live_menu) ---------- */
  d.addEventListener('c:continue', () => {
    const all = pm7.$$('#ci form.cm-cont button');
    if (all.length) all[all.length - 1].click();
    else toast('No reply to continue from yet');
  });
  d.addEventListener('c:usage', () => {
    const st = d.getElementById('chat-stats');
    if (!st || !st.querySelector('summary')) { toast('Usage is not loaded yet; try again in a moment'); return; }
    st.open = true;
    st.scrollIntoView({ block: 'nearest' });
    st.querySelector('summary').focus({ preventScroll: true });
  });
  d.addEventListener('c:btw', () => btw(''));

  pm7.ready(() => {
    if (!sess()) return;
    initTabs();
    applySizes();
    cmpInit();
    pinInit();
    later();
  });
})();
