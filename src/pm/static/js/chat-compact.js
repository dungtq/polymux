/* Main chat Compact view: the same chat items as Detail, regrouped on the page.
   Mode: localStorage 'pm.chatView' = compact (default) | detail, per browser; #ci gets data-cv; every
   [data-chat-view] button (templates/_chatview.html) gets aria-pressed; a 'storage' event re-applies it (the
   drawer iframe follows its parent page). A mode change dispatches pm:chat-view; chat-items.js then settles.
   Compact, per user message (all the Claude turns until the next one): the user bubble; one "Working notes"
   block (section.ci-cn) with every reply except the final one as a list item and their tool groups in one
   folded total (details.ci-cnt); the final reply (the last reply, only once the span is done and only when it
   is long; a short one stays the last note and "took …" goes in the notes header) on its own with "took …" and
   its tool groups folded at its bottom; the end items (hidden by CSS). Item nodes are MOVED, never
   re-rendered (ids, timers, poll updates by id keep working); only nodes that are out of place move.
   Exposes window.pm.ciCompact {plan, layout, mode, setMode}; plan is pure (tests run it in node). */
(function () {
  'use strict';
  var w = typeof window !== 'undefined' ? window : globalThis;
  var pm = w.pm = w.pm || {};
  var KEY = 'pm.chatView', memo = null;

  // ---- plan (pure) ----
  // Grouped per user message, not per Claude turn (a turn also ends while Claude waits for a background task):
  // a span runs from a user message to the next one. Its replies are working notes, except the last one, which
  // is the final bubble once the span is done (it ends on a turn end, no background task running) and the reply
  // is long; a short last reply stays the last note. "took" = the span's turn durations added up.
  // ds: [{k: 'user'|'text'|'tg'|'end'|'ob', seq, parent, short (text), run (tg), dur (end, s)}] in seq order
  // (bubbles last) -> slots: {t:'node', i} | {t:'notes', key, texts:[i], tools:[i], took} |
  // {t:'final', i, tools:[i], took} (i = index in ds)
  function plan(ds) {
    var out = [], obs = [], spans = [], cur = null;
    ds.forEach(function (d, i) {
      if (d.k === 'ob') { obs.push(i); return; }
      if (!cur || d.k === 'user') { cur = []; spans.push(cur); }
      cur.push(i);
    });
    spans.forEach(function (s) { spanSlots(ds, s, out); });
    obs.forEach(function (i) { out.push({ t: 'node', i: i }); });
    return out;
  }
  function spanSlots(ds, idx, out) {
    var user = ds[idx[0]].k === 'user' ? idx[0] : -1, body = user >= 0 ? idx.slice(1) : idx;
    var ends = [], texts = [], tools = [], finTools = [], fin = -1, sec = 0, past = false, j;
    var last = body.length ? ds[body[body.length - 1]] : null;
    var done = !!last && last.k === 'end' && !body.some(function (i) { return ds[i].k === 'tg' && ds[i].run; });
    body.forEach(function (i) { if (ds[i].k === 'end') sec += ds[i].dur || 0; });
    var took = sec > 0 ? dur(sec) : '';
    if (done) for (j = body.length - 1; j >= 0; j--) if (ds[body[j]].k === 'text') { fin = body[j]; break; }
    if (fin >= 0 && ds[fin].short) fin = -1;
    body.forEach(function (i) {
      var d = ds[i];
      if (d.k === 'end') { ends.push(i); return; }
      if (i === fin) { past = true; return; }
      if (d.k === 'tg' && fin >= 0 && (past || d.parent === ds[fin].seq)) { finTools.push(i); return; }
      (d.k === 'text' ? texts : tools).push(i);
    });
    // tools before the final with no note beside them belong to the final (no tools-only block)
    if (fin >= 0 && !texts.length) { finTools = tools.concat(finTools); tools = []; }
    if (user >= 0) out.push({ t: 'node', i: user });
    if (texts.length || tools.length) {
      out.push({ t: 'notes', key: ds[idx[0]].seq, texts: texts, tools: tools, took: done && fin < 0 ? took : '' });
    }
    if (fin >= 0) out.push({ t: 'final', i: fin, tools: finTools, took: took });
    ends.forEach(function (i) { out.push({ t: 'node', i: i }); });
  }
  // '45s' | '5m' | '2h 3m' | '1d 4h' <-> seconds (the end item's duration, pm.core.timefmt.duration)
  var UNIT = { d: 86400, h: 3600, m: 60, s: 1 };
  function secs(s) {
    var n = 0;
    String(s || '').replace(/(\d+)\s*([dhms])/g, function (_, v, u) { n += parseInt(v, 10) * UNIT[u]; return ''; });
    return n;
  }
  function dur(n) {
    n = Math.max(0, Math.floor(n));
    if (n < 60) return n + 's';
    if (n < 3600) return Math.floor(n / 60) + 'm';
    if (n < 86400) return Math.floor(n / 3600) + 'h ' + Math.floor(n % 3600 / 60) + 'm';
    return Math.floor(n / 86400) + 'd ' + Math.floor(n % 86400 / 3600) + 'h';
  }
  // a reply is short when it is plain text of at most 2 lines (paragraphs + line breaks) and SHORT_CHARS
  var SHORT_LINES = 2, SHORT_CHARS = 280, BLOCKS = 'h1, h2, h3, h4, h5, h6, ul, ol, pre, table, blockquote, hr, img';
  function isShort(x) {
    if (!x) return true;
    if (x.querySelector(BLOCKS)) return false;
    var lines = Math.max(1, x.querySelectorAll('p').length) + x.querySelectorAll('br').length;
    return lines <= SHORT_LINES && (x.textContent || '').trim().length <= SHORT_CHARS;
  }

  // ---- mode ----
  function norm(m) { return m === 'detail' ? 'detail' : 'compact'; }
  function mode() {
    if (memo) return memo;
    var v = null;
    try { v = w.localStorage.getItem(KEY); } catch (e) { v = null; }
    return norm(v);
  }

  var C = pm.ciCompact = { plan: plan, layout: layout, mode: mode, setMode: setMode, secs: secs, dur: dur };
  if (typeof document === 'undefined' || !document.addEventListener) return;

  function apply() {
    var m = mode(), ci = document.getElementById('ci'), changed = false;
    if (ci && ci.getAttribute('data-cv') !== m) { ci.setAttribute('data-cv', m); changed = true; }
    document.querySelectorAll('[data-chat-view]').forEach(function (b) {
      var p = String(b.getAttribute('data-chat-view') === m);
      if (b.getAttribute('aria-pressed') !== p) b.setAttribute('aria-pressed', p);
    });
    if (changed) document.dispatchEvent(new CustomEvent('pm:chat-view', { detail: { mode: m } }));
  }
  function setMode(m) {
    memo = norm(m);
    try { w.localStorage.setItem(KEY, memo); } catch (e) { /* memo keeps it for this page */ }
    apply();
  }

  // ---- layout (DOM) ----
  var ITEMS = '.ci-user, .ci-text, .ci-tg, .ci-end';
  function el(tag, cls, text) {
    var n = document.createElement(tag);
    if (cls) n.className = cls;
    if (text != null) n.textContent = text;
    return n;
  }
  function num(n, a) { return parseInt(n.getAttribute(a), 10) || 0; }
  function kind(n) {
    var c = n.classList;
    return c.contains('ci-ob') ? 'ob' : c.contains('ci-user') ? 'user' : c.contains('ci-text') ? 'text'
      : c.contains('ci-tg') ? 'tg' : 'end';
  }
  function child(p, cls) {
    for (var c = p && p.firstElementChild; c; c = c.nextElementSibling) if (c.classList.contains(cls)) return c;
    return null;
  }
  function byId(list, id) { var n = document.getElementById(id); return n && list.contains(n) ? n : null; }
  // item nodes in seq order, outbox bubbles last in their current order
  function collect(list) {
    var all = Array.prototype.slice.call(list.querySelectorAll(ITEMS)), its = [], obs = [];
    all.forEach(function (n, j) { if (kind(n) === 'ob') obs.push(n); else its.push({ n: n, s: num(n, 'data-seq'), j: j }); });
    its.sort(function (a, b) { return a.s - b.s || a.j - b.j; });
    return its.map(function (x) { return x.n; }).concat(obs);
  }
  // make box's element children exactly `want`, in order: nodes already in place are not touched
  function order(box, want) {
    var keep = new Set(want), ref = box.firstElementChild;
    want.forEach(function (n) {
      while (ref && !keep.has(ref)) ref = ref.nextElementSibling;
      if (n === ref) { ref = ref.nextElementSibling; return; }
      box.insertBefore(n, ref);
    });
    Array.prototype.slice.call(box.children).forEach(function (c) { if (!keep.has(c)) c.remove(); });
  }
  function setOpen(d, v) { if (d.open !== v) d.open = v; }
  function setAttr(n, a, v) {
    if (v == null) { if (n.hasAttribute(a)) n.removeAttribute(a); } else if (n.getAttribute(a) !== v) n.setAttribute(a, v);
  }
  // "after: <first line of the reply the group follows>"
  function after(g) {
    var p = num(g, 'data-parent'), t = p && document.getElementById('i-' + p);
    if (!t) return 'the prompt';
    var x = child(t, 'chat-text') || t;
    return (x.textContent || '').trim().split('\n')[0].slice(0, 60);
  }
  // one folded total for the tool groups (forced open inside it); reused by id, else the one already in scope
  function tools(list, scope, id, groups, head, opened, used) {
    if (!groups.length) return null;
    var c = byId(list, id), old = child(scope, 'ci-cnt');
    if (!c || used.has(c)) c = old && !used.has(old) ? old : null;
    if (!c) {
      c = el('details', 'ci-cnt');
      var sm = child(groups[0], 'ci-tg-h').cloneNode(true);
      sm.removeAttribute('data-after');
      sm.removeAttribute('tabindex');
      c.appendChild(sm);
      c.appendChild(el('div', 'ci-cnt-rows'));
      if (opened[id]) c.open = true;
    }
    used.add(c);
    setAttr(c, 'id', id);
    c.classList.toggle('ci-cnt-one', groups.length === 1);
    groups.forEach(function (g) {
      setOpen(g, true);
      var h = child(g, 'ci-tg-h');
      if (h) { setAttr(h, 'data-after', groups.length > 1 ? after(g) : null); setAttr(h, 'tabindex', '-1'); }
    });
    order(child(c, 'ci-cnt-rows'), groups);
    if (head) head(c);
    return c;
  }
  // the agent kind (left bar colour): from a reply of the block, else any reply or the page header
  function kindOf(list, texts) {
    var t = texts[0] || list.querySelector('.ci-text[data-kind]') || document.querySelector('.agent-head[data-kind]');
    return t ? t.getAttribute('data-kind') : null;
  }
  function notes(list, s, nodes, head, opened, used) {
    var id = 'cn-' + s.key, texts = s.texts.map(at), groups = s.tools.map(at), box = byId(list, id), seq = 0;
    function at(i) { return nodes[i]; }
    if (!box || used.has(box)) {
      box = null;
      [].concat(texts, groups).some(function (n) {
        var c = n.closest('.ci-cn');
        if (c && list.contains(c) && !used.has(c)) box = c;
        return !!box;
      });
    }
    if (!box) {
      box = el('section', 'ci-cn');
      var h = el('header', 'ci-cn-h');
      h.appendChild(el('strong', null, 'Working notes'));
      h.appendChild(document.createTextNode(' '));
      h.appendChild(el('span', 'ci-cn-n'));
      h.appendChild(el('span', 'ci-took'));
      box.appendChild(h);
      box.appendChild(el('ul', 'ci-cn-list'));
    }
    used.add(box);
    setAttr(box, 'id', id);
    [].concat(texts, groups).forEach(function (n) { var q = num(n, 'data-seq'); if (q && (!seq || q < seq)) seq = q; });
    setAttr(box, 'data-seq', String(seq));
    setAttr(box, 'data-kind', kindOf(list, texts));
    var hd = child(box, 'ci-cn-h'), ul = child(box, 'ci-cn-list'), cnt = child(hd, 'ci-cn-n');
    var label = '· ' + texts.length + ' step' + (texts.length === 1 ? '' : 's');
    if (cnt && cnt.textContent !== label) cnt.textContent = label;
    var tk = child(hd, 'ci-took'), tl = s.took ? '· took ' + s.took : '';
    if (tk && tk.textContent !== tl) tk.textContent = tl;
    hd.hidden = !texts.length;
    ul.hidden = !texts.length;
    order(ul, texts.map(function (t) {
      var li = t.parentNode;
      if (!li || !li.classList || !li.classList.contains('ci-cn-li')) { li = el('li', 'ci-cn-li'); li.appendChild(t); }
      return li;
    }));
    var c = tools(list, box, 'cnt-' + s.key, groups, head, opened, used);
    order(box, c ? [hd, ul, c] : [hd, ul]);
    return box;
  }
  function final(list, s, nodes, head, opened, used) {
    var f = nodes[s.i], meta = child(f, 'chat-meta'), took = meta && child(meta, 'ci-took');
    f.classList.add('ci-final');
    if (s.took && meta) {
      if (!took) {
        took = el('span', 'ci-took muted');
        var st = child(meta, 'stamp');
        meta.insertBefore(took, st ? st.nextSibling : null);
      }
      if (took.textContent !== '· took ' + s.took) took.textContent = '· took ' + s.took;
    } else if (took) took.remove();
    var c = tools(list, f, 'cft-' + num(f, 'data-seq'), s.tools.map(function (i) { return nodes[i]; }), head, opened, used);
    if (c && f.lastElementChild !== c) f.appendChild(c);
    return f;
  }
  // list: .ci-items; mode: 'compact' | 'detail'; head(details): recompute a group header from its rows (deep);
  // opened: id -> open, the groups and folded totals the user opened
  function layout(list, m, head, opened) {
    if (!list) return;
    opened = opened || {};
    var nodes = collect(list), used = new Set(), finals = new Set(), top = [];
    if (norm(m) === 'compact') {
      plan(nodes.map(function (n) {
        var k = kind(n), d = { k: k, seq: num(n, 'data-seq'), parent: num(n, 'data-parent') };
        if (k === 'text') d.short = isShort(child(n, 'chat-text'));
        else if (k === 'tg') d.run = !!n.querySelector('.ci-ts.is-run.is-bg');
        else if (k === 'end') d.dur = secs(n.getAttribute('data-dur'));
        return d;
      })).forEach(function (s) {
        if (s.t === 'node') top.push(nodes[s.i]);
        else if (s.t === 'notes') top.push(notes(list, s, nodes, head, opened, used));
        else { var f = final(list, s, nodes, head, opened, used); finals.add(f); top.push(f); }
      });
    } else {
      var back = nodes.filter(function (n) { return kind(n) === 'tg' && n.closest('.ci-cnt'); });
      top = nodes;
      order(list, top);
      back.forEach(function (g) {
        setOpen(g, !!opened[g.id]);
        var h = child(g, 'ci-tg-h');
        if (h) { setAttr(h, 'data-after', null); setAttr(h, 'tabindex', null); }
      });
    }
    if (top !== nodes) order(list, top);
    list.querySelectorAll('.ci-final').forEach(function (f) {
      if (finals.has(f)) return;
      f.classList.remove('ci-final');
      var t = child(child(f, 'chat-meta'), 'ci-took');
      if (t) t.remove();
    });
    list.querySelectorAll('.ci-cn, .ci-cnt').forEach(function (x) { if (!used.has(x)) x.remove(); });
  }

  // ---- wiring ----
  document.addEventListener('click', function (e) {
    var b = e.target.closest && e.target.closest('[data-chat-view]');
    if (!b) return;
    e.preventDefault();
    setMode(b.getAttribute('data-chat-view'));
  });
  w.addEventListener('storage', function (ev) { if (ev.key === KEY || ev.key === null) { memo = null; apply(); } });
  document.addEventListener('htmx:afterSettle', apply);
  apply();
  C.apply = apply;
})();
