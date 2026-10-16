/* The one composer (templates/_composer_macros.html). pm.composer.init(root) runs for every [data-composer] on load and on
 * htmx:load; everything is per root, found through the root's [data-cmp-*] hooks (no ids):
 * - autosize (the textarea grows with its text up to the CSS max-height), placeholder (the short data-ph-short under 700px,
 *   updated by the pm:composer event after a model / effort switch);
 * - the send key, data-send-key: "enter" = Enter sends, Shift+Enter is a newline (on touch screens Enter is a newline);
 *   "mod-enter" = Ctrl/Cmd+Enter sends, Enter is a newline. Ctrl/Cmd+Enter always sends;
 * - the draft (pm.store "pm.draft:<data-draft-key>": survives a reload, also ended -> live) and the history (ArrowUp in an
 *   empty box recalls this page's earlier messages, ArrowDown goes forward, Esc restores the draft);
 * - empty / busy state: Send is off while the box is empty or a post is on its way; [data-label-empty] / [data-label-text]
 *   buttons follow the box (ended: Resume / Resume and send, Fork / Fork and send);
 * - Send <-> Stop: with a [data-cmp-stop] form, the green button interrupts the agent while it runs (pm:agent_state, first
 *   value from data-state) and the box is empty; typing turns it back into Send. The stop form keeps its in-page confirm;
 * - the Keys toggle (Chat tab <-> Pane tab);
 * - answers aimed at a [data-cmp-flash] / [data-cmp-slash-card] slot land in the slot of the composer that asked, and a
 *   JSON error ({"detail": ...}) reads as a notice.
 * Transport (data-send): "htmx" = the form's htmx post, chat-send.js adds the outbox bubble; "form" = the form's own post,
 * the box and the draft clear on success; "stream" (quick chat) = the form's submit event, read by its owner. A composer
 * inside a bigger form (Launch) uses that form, and with no Send of its own the send key clicks the form's first submit
 * button. pm.composer.init(root, {onSend: fn(st) -> false cancels, onStop: fn(st)}) adds hooks (also to a root that is
 * already set up): onSend for other transports (Continue: the popup's Start), onStop makes Send a Stop while
 * pm.composer.running(root, true) and the box is empty (quick chat: abort the answer). pm.composer.insert(el, text) puts
 * text at the cursor of el's composer -> {ok, cut}. pm.composer.use(fn(root, st)) adds a per-root plugin (dock, attach,
 * send). External file (strict CSP). */
(function () {
  'use strict';
  var pm = window.pm = window.pm || {};
  var HIST_CAP = 100, HIST_SRC = '#ci .ci-user .chat-copy[data-raw]', SLOTS = ['[data-cmp-flash]', '[data-cmp-slash-card]'];

  // ---- pure helpers (tested under node) ----
  // History: bubbles in page order, then this page's sends not shown yet; blanks dropped, consecutive repeats folded,
  // the newest `cap` kept (oldest first).
  function histList(dom, sent, cap) {
    var all = dom.concat(sent.filter(function (s) { return dom.indexOf(s) < 0; })), out = [];
    all.forEach(function (s) { if (s && s.trim() && s !== out[out.length - 1]) out.push(s); });
    return out.slice(-cap);
  }
  // Next browse position: n entries, pos n = the draft; dir -1 = older, +1 = newer; clamped.
  function histMove(n, pos, dir) { return Math.max(0, Math.min(n, pos + dir)); }
  // Does this keydown send? key = data-send-key; fine = a mouse / trackpad device.
  function sends(key, e, fine) {
    if (e.key !== 'Enter' || e.isComposing || e.altKey) return false;
    if (e.ctrlKey || e.metaKey) return !e.shiftKey;
    return key !== 'mod-enter' && !!fine && !e.shiftKey;
  }
  pm.composer = { histList: histList, histMove: histMove, sends: sends };
  if (typeof document === 'undefined' || !document.addEventListener) return;

  var d = document, roots = [], plugins = [];
  var fineMq = window.matchMedia ? window.matchMedia('(hover: hover) and (pointer: fine)') : null;
  var narrowMq = window.matchMedia ? window.matchMedia('(max-width: 699px)') : null;
  function q(root, sel) { return root.querySelector(sel); }
  function rootOf(el) { return el && el.closest ? el.closest('[data-composer]') : null; }
  function live() { roots = roots.filter(function (st) { return d.contains(st.root); }); return roots; }
  function isEmpty(st) { return !st.ta.value.trim() && !q(st.root, '[data-cmp-chips] .catt'); }

  function grow(st) {
    var ta = st.ta;
    ta.style.height = 'auto';
    var border = ta.offsetHeight - ta.clientHeight;
    ta.style.height = ta.scrollHeight > ta.clientHeight ? (ta.scrollHeight + border) + 'px' : '';
  }
  function setPh(st) { st.ta.setAttribute('placeholder', st.phShort && narrowMq && narrowMq.matches ? st.phShort : st.phFull); }
  function saveDraft(st) {
    if (!st.dk || !pm.store) return;
    if (st.ta.value) pm.store.set(st.dk, st.ta.value); else pm.store.remove(st.dk);
  }

  // ---- state of the buttons ----
  function relabel(b, empty) {
    if (!b || !b.hasAttribute('data-label-text') || b.getAttribute('aria-busy') === 'true') return;
    var t = b.getAttribute(empty ? 'data-label-empty' : 'data-label-text');
    if (b.textContent !== t) b.textContent = t;
  }
  function setStop(b, on) {
    if (b.classList.contains('is-stop') === on) return;
    if (!b.hasAttribute('data-send-title')) b.setAttribute('data-send-title', b.getAttribute('title') || '');
    var t = b.getAttribute(on ? 'data-stop-title' : 'data-send-title');
    b.classList.toggle('is-stop', on);
    b.type = on ? 'button' : 'submit';
    b.title = t; b.setAttribute('aria-label', t);
    if (b.hasAttribute('data-tip')) b.setAttribute('data-tip', t);
  }
  function sync(st) {
    var b = st.send, empty = isEmpty(st);
    st.root.classList.toggle('is-empty', empty);
    if (!b) return;
    if (b.hasAttribute('data-stop-title')) setStop(b, empty && st.running && !!(st.stop || st.onStop) && !st.busy);
    relabel(b, empty); relabel(st.alt, empty);
    if (st.alt && st.alt.getAttribute('aria-busy') !== 'true') st.alt.disabled = st.busy;
    if (b.getAttribute('aria-busy') === 'true') return;  // agentmode.js: "Resuming..." until the page switches
    b.disabled = st.busy || (empty && !b.classList.contains('is-stop') && !b.hasAttribute('data-cmp-empty-ok'));
  }

  // ---- send / stop ----
  function stop(st) {
    var f = st.stop;
    if (st.onStop) { st.onStop(st); return; }
    if (!f) return;
    if (f.requestSubmit) f.requestSubmit(); else if (window.htmx) window.htmx.trigger(f, 'submit');
  }
  function submit(st) {
    var b = st.send || st.form.querySelector('button[type=submit]');  // no Send of its own (Launch): the form's first one
    if (isEmpty(st) || (b && (b.disabled || b.classList.contains('is-stop')))) return;
    if (st.onSend && st.onSend(st) === false) return;
    if (!b) return;
    if (b !== st.send) b.click();  // a click: htmx sends that button's own name / value (Launch: open=1)
    else if (st.form.requestSubmit) st.form.requestSubmit(b.type === 'submit' ? b : undefined); else st.form.submit();
  }

  // text at the cursor (the box's maxlength wins); input event: grow, draft, Send state
  function insert(el, text) {
    var r = rootOf(el), ta = r && q(r, '[data-cmp-input]'), t = String(text);
    var room = !ta ? 0 : ta.maxLength > 0 ? ta.maxLength - ta.value.length : t.length;
    if (room <= 0) return { ok: false, cut: false };
    var cut = t.length > room, a = ta.selectionStart == null ? ta.value.length : ta.selectionStart;
    var e = ta.selectionEnd == null ? a : ta.selectionEnd;
    if (cut) t = t.slice(0, room);
    ta.value = ta.value.slice(0, a) + t + ta.value.slice(e);
    ta.focus();
    try { ta.setSelectionRange(a + t.length, a + t.length); } catch (x) { /* not focusable */ }
    ta.dispatchEvent(new Event('input', { bubbles: true }));
    return { ok: true, cut: cut };
  }

  // ---- history (bubble phase: open slash / @ popups take the arrows first, in capture) ----
  function caretOn(ta, first) {
    var a = ta.selectionStart, b = ta.selectionEnd;
    if (a !== b) return false;
    return first ? ta.value.slice(0, a).indexOf('\n') < 0 : ta.value.indexOf('\n', b) < 0;
  }
  function recall(st, s) {
    st.recalling = true;
    st.ta.value = s;
    st.ta.setSelectionRange(s.length, s.length);
    st.ta.dispatchEvent(new Event('input', { bubbles: true }));
    st.recalling = false;
  }
  function domHist(st) {
    if (!st.root.hasAttribute('data-agent')) return [];
    return Array.prototype.map.call(d.querySelectorAll(HIST_SRC), function (b) { return b.getAttribute('data-raw') || ''; });
  }
  function history(st, e) {
    if (e.ctrlKey || e.metaKey || e.shiftKey || e.altKey) return;
    var k = e.key, ta = st.ta, on = st.hist !== null && ta.value === st.hist[st.hpos];
    if (k === 'ArrowUp' && (on ? caretOn(ta, true) : (st.hist === null && ta.value === ''))) {
      if (st.hist === null) {
        var h = histList(domHist(st), st.sent, HIST_CAP);
        if (!h.length) return;
        st.hist = h; st.hpos = h.length; st.hdraft = ta.value;
      }
      e.preventDefault();
      var p = histMove(st.hist.length, st.hpos, -1);
      if (p !== st.hpos) { st.hpos = p; recall(st, st.hist[p]); }
    } else if (k === 'ArrowDown' && on && caretOn(ta, false)) {
      e.preventDefault();
      st.hpos = histMove(st.hist.length, st.hpos, 1);
      if (st.hpos < st.hist.length) recall(st, st.hist[st.hpos]);
      else { var dr = st.hdraft; st.hist = null; recall(st, dr); }
    } else if (k === 'Escape' && st.hist !== null) {
      e.preventDefault();
      var back = st.hdraft; st.hist = null; recall(st, back);
    }
  }

  // ---- the Keys toggle: Chat tab <-> Pane tab (the keypad lives in the Pane tab) ----
  function keysSync() {
    var pane = d.getElementById('tab-pane');
    live().forEach(function (st) {
      var b = q(st.root, '[data-keys]');
      if (b) b.setAttribute('aria-pressed', pane && pane.checked ? 'true' : 'false');
    });
  }
  function keysFlip() {
    var pane = d.getElementById('tab-pane'), chat = d.getElementById('tab-chat');
    if (!pane || !chat) return;
    var to = pane.checked ? chat : pane;
    to.checked = true;
    to.dispatchEvent(new Event('change', { bubbles: true }));
  }

  function mine(st, e) { var el = e.detail && e.detail.elt; return !!el && (el === st.form || el === st.alt); }
  function wire(st) {
    var ta = st.ta, root = st.root;
    ta.addEventListener('input', function () { if (!st.recalling) st.hist = null; grow(st); saveDraft(st); sync(st); });
    ta.addEventListener('keydown', function (e) {
      if (e.defaultPrevented) return;
      if (sends(st.key, e, fineMq && fineMq.matches)) { e.preventDefault(); submit(st); return; }
      history(st, e);
    });
    root.addEventListener('click', function (e) {
      var t = e.target;
      if (!t || !t.closest) return;
      var b = t.closest('[data-cmp-send]');
      if (b && b.classList.contains('is-stop')) { e.preventDefault(); stop(st); return; }
      if (t.closest('[data-keys]')) keysFlip();
    });
    root.addEventListener('htmx:beforeRequest', function (e) {
      if (!mine(st, e)) return;
      if (e.detail.elt === st.form && ta.value.trim()) { st.sent.push(ta.value); if (st.sent.length > HIST_CAP) st.sent.shift(); }
      st.hist = null; st.busy = true; sync(st);
    });
    root.addEventListener('htmx:afterRequest', function (e) {
      if (!mine(st, e)) return;
      st.busy = false;
      if (e.detail.successful && st.transport === 'form') ta.value = '';
      grow(st); saveDraft(st); sync(st);
    });
    root.addEventListener('pm:composer', function (e) {  // HX-Trigger of a model / effort switch {ph, phShort}
      var x = e.detail || {};
      if (typeof x.ph !== 'string' || typeof x.phShort !== 'string') return;
      st.phFull = x.ph; st.phShort = x.phShort;
      ta.setAttribute('data-ph-short', x.phShort);
      setPh(st);
    });
  }

  function running0(root) {
    if (!root.hasAttribute('data-agent')) return false;  // quick chat, Launch, Continue: no agent (qchat sets it itself)
    var s = root.getAttribute('data-state');
    if (s) return s === 'running';
    var b = d.querySelector('#agent-state .badge');
    return !!(b && b.classList.contains('st-running'));
  }
  function init(root, opts) {
    if (!root || !root.getAttribute) return null;
    var had = root.__cmp;
    if (had) {
      if (opts) { had.onSend = opts.onSend || had.onSend; had.onStop = opts.onStop || had.onStop; sync(had); }
      return had;
    }
    var ta = q(root, '[data-cmp-input]'), form = q(root, '[data-cmp-form]') || (root.closest && root.closest('form'));
    if (!ta || !form) return null;
    var dk = root.getAttribute('data-draft-key');
    var st = root.__cmp = {
      root: root, ta: ta, form: form, send: q(root, '[data-cmp-send]'), alt: q(root, '[data-cmp-alt]'), stop: q(root, '[data-cmp-stop]'),
      key: root.getAttribute('data-send-key') || 'enter', transport: root.getAttribute('data-send') || 'htmx',
      dk: dk ? 'pm.draft:' + dk : '', running: running0(root), busy: false, onSend: opts && opts.onSend, onStop: opts && opts.onStop,
      sent: [], hist: null, hpos: 0, hdraft: '', recalling: false,
      phFull: ta.getAttribute('placeholder') || '', phShort: ta.getAttribute('data-ph-short') || ''
    };
    roots.push(st);
    var saved = st.dk && pm.store ? pm.store.get(st.dk) : null;
    if (saved && !ta.value) ta.value = saved;
    wire(st);
    setPh(st); grow(st); sync(st); keysSync();
    plugins.forEach(function (fn) { fn(root, st); });
    return st;
  }
  function use(fn) { plugins.push(fn); live().forEach(function (st) { fn(st.root, st); }); }
  function scan(el) {
    if (!el || !el.querySelectorAll) return;
    if (el.matches && el.matches('[data-composer]')) init(el);
    Array.prototype.forEach.call(el.querySelectorAll('[data-composer]'), function (r) { init(r); });
  }
  function slot(el) { var r = rootOf(el); return (r && q(r, '[data-cmp-flash]')) || d.getElementById('flash'); }

  // answers for a composer slot go to the slot of the composer that asked (pm.js already turns a JSON error into a notice)
  d.addEventListener('htmx:beforeSwap', function (e) {
    var x = e.detail || {}, root = rootOf(x.requestConfig && x.requestConfig.elt), t = x.target;
    if (!root || !t || !t.matches) return;
    SLOTS.forEach(function (s) { if (t.matches(s) && !root.contains(t)) x.target = q(root, s) || t; });
  });
  d.addEventListener('pm:agent_state', function (e) {
    var x = e.detail;
    if (!x || !x.id) return;
    live().forEach(function (st) {
      if (st.root.getAttribute('data-agent') === x.id) { st.running = x.state === 'running'; sync(st); }
    });
  });
  d.addEventListener('change', function (e) { if (e.target && e.target.name === 'agent-tab') keysSync(); });
  window.addEventListener('pagehide', function () { live().forEach(saveDraft); });
  var raf = 0;
  window.addEventListener('resize', function () {
    if (!raf) raf = window.requestAnimationFrame(function () { raf = 0; live().forEach(grow); });
  });
  if (narrowMq && narrowMq.addEventListener) narrowMq.addEventListener('change', function () { live().forEach(setPh); });
  d.addEventListener('htmx:load', function (e) { scan(e.target); });
  if (d.readyState === 'loading') d.addEventListener('DOMContentLoaded', function () { scan(d); }); else scan(d);

  pm.composer.init = init;
  pm.composer.use = use;
  pm.composer.root = rootOf;
  pm.composer.slot = slot;
  pm.composer.sync = function (el) { var r = rootOf(el); if (r && r.__cmp) { grow(r.__cmp); sync(r.__cmp); } };
  pm.composer.running = function (el, on) {  // with `on`: set it (quick chat while its answer streams)
    var r = rootOf(el), st = r && r.__cmp;
    if (st && on !== undefined) { st.running = !!on; sync(st); }
    return !!(st && st.running);
  };
  pm.composer.insert = insert;
})();
