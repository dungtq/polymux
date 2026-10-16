/* Chat items (docs/CHAT-SYNC.md) on the agent page, the ended page and the Builder chat.
   #ci holds the append-only conversation from GET /a/<id>/items, #ci-pending pm's own composer messages
   that are not in the transcript yet (outbox). Only #chat (the live strip, htmx) is ever replaced.
   1. first load: the newest page; then, on pm:chat_items / pm:chat_outbox for this agent (live.js re-
      dispatches /events) and every 5 s as a fallback (15 s ended; an empty page re-asks a few times
      after 1.5 s), /items?after_rev=<cursor> (new AND changed items);
   2. a new item is inserted by seq; a changed tool row (li#i-<seq>) is replaced alone and its group
      header recomputed from the rows; a user bubble only gets its state + actions swapped;
   3. tool groups are folded by default; groups the user opened stay open while on the page;
   4. running timers tick here from data-start (server clock via X-Now); "waiting" after 10 s;
   5. outbox actions (Send now / Edit / Cancel / Retry) POST with pm.fetch + CSRF; Cancel asks first;
   6. Load older keeps the reading position (pm.chatScroll from chatlog.js keeps the bottom);
   7. outbox bubbles: waiting ones (saved / wait / failed) sit in #ci-pending under the running turn; once
      delivered (data-flow: sending / in pane / queued / done) a bubble moves, once, to the end of the
      conversation and stays the newest there until its user item arrives and takes its place.
   External file only (strict CSP). Exposes window.pm.chatItems {addOutbox, refresh}, window.pm.ciRules. */
(function () {
  'use strict';
  var pm = window.pm = window.pm || {};
  // placement rules (pure; tests/web/test_chatitems_templates.py runs them in node)
  var rules = pm.ciRules = {
    // an outbox bubble: delivered (flow) or not, and where it is now ('none' | 'pending' | 'flow') ->
    // 'flow' (append to the conversation), 'pending' (append under the running turn) or 'stay'
    obTarget: function (flow, at) {
      if (at === 'flow') return 'stay';
      if (flow) return 'flow';
      return at === 'pending' ? 'stay' : 'pending';
    },
    // index in nodes [{seq, ob}] where an item with seq s goes: after the last item with seq <= s and
    // before the outbox bubbles that follow it
    slot: function (nodes, s) {
      var i = nodes.length;
      while (i > 0 && (nodes[i - 1].ob || nodes[i - 1].seq > s)) i--;
      return i;
    },
    // an item inserted at slot i replaces the bubble at index j when only bubbles lie between them
    replaces: function (nodes, i, j) {
      if (j < i) return false;
      for (var k = i; k < j; k++) if (!nodes[k].ob) return false;
      return true;
    },
    ago: function (d) {
      d = Math.max(0, Math.floor(d));
      if (d < 60) return d + 's ago';
      if (d < 3600) return Math.floor(d / 60) + 'm ago';
      if (d < 86400) return Math.floor(d / 3600) + 'h ago';
      return Math.floor(d / 86400) + 'd ago';
    },
    // kinds ['user' | 'text' | 'tool' | 'end'] in list order -> index of the reply that keeps New chat /
    // Replace / Canvas: the nearest reply before the last turn end (tool groups may lie between), else -1;
    // -1 too while a turn is running (a prompt, reply or tool after the last turn end)
    doneLast: function (kinds) {
      var e = kinds.lastIndexOf('end'), i = e;
      for (var k = e + 1; k < kinds.length; k++) {
        if (kinds[k] === 'user' || kinds[k] === 'text' || kinds[k] === 'tool') return -1;
      }
      while (--i >= 0) {  // stop at the turn's prompt: a turn without a text reply marks nothing
        if (kinds[i] === 'text') return i;
        if (kinds[i] === 'user') return -1;
      }
      return -1;
    }
  };
  var root = document.getElementById('ci');
  if (!root) return;
  var AID = root.getAttribute('data-agent') || '';
  var BASE = '/a/' + encodeURIComponent(AID);
  var EMBED = /[?&]embed=1(&|$)/.test(window.location.search);
  var POLL_MS = root.hasAttribute('data-ended') ? 15000 : 5000;
  var WAIT_S = 10, FRESH_MS = 8000, QUICK_MS = 1500, QUICK_N = 6;
  var cursor = null, skew = 0, inflight = false, again = false, oldest = 0, lastFetch = 0, quick = 0;
  var opened = {};  // group id -> open: groups the user opened stay open

  function kids(n) { return n ? Array.prototype.slice.call(n.children) : []; }
  function seqOf(n) { return (n && n.getAttribute && parseInt(n.getAttribute('data-seq'), 10)) || 0; }
  function list() { return root.querySelector('.ci-items'); }
  function pending() { return document.getElementById('ci-pending'); }
  function el(tag, cls, text) {
    var n = document.createElement(tag);
    if (cls) n.className = cls;
    if (text != null) n.textContent = text;
    return n;
  }
  function scroll(fn, arg) { var s = pm.chatScroll; if (s && s[fn]) s[fn](arg); }
  function parse(html) { return new DOMParser().parseFromString('<!doctype html><body>' + html, 'text/html').body; }
  // only the newest reply with a turn end after it shows the reply actions (CSS: .ci-done-last)
  function markDone() {
    var l = list(), k = l ? Array.prototype.slice.call(l.querySelectorAll('.ci-user, .ci-text, .ci-tg, .ci-end')) : [];
    var i = rules.doneLast(k.map(function (c) {
      return c.classList.contains('ci-text') ? 'text' : c.classList.contains('ci-end') ? 'end'
        : c.classList.contains('ci-user') ? 'user' : c.classList.contains('ci-tg') ? 'tool' : '';
    }));
    k.forEach(function (c, j) { c.classList.toggle('ci-done-last', j === i); });
  }
  // the Compact view (chat-compact.js) regroups the item nodes on every settle; Detail flattens them back
  function settle(n) {
    var cv = pm.ciCompact;
    if (cv) cv.layout(list(), cv.mode(), head, opened);
    markDone();
    if (n && window.htmx) { try { window.htmx.process(n); } catch (x) { /* ignore */ } }
    root.dispatchEvent(new CustomEvent('pm:ci-update', { bubbles: true }));
  }
  function flash(msg) {
    var f = document.getElementById('flash');
    if (!f) return;
    f.textContent = '';
    f.appendChild(el('p', 'flash err', msg));
  }

  // ---- requests ----
  function url(q) {
    var p = [];
    if (q) p.push(q);
    if (EMBED) p.push('embed=1');
    return BASE + '/items' + (p.length ? '?' + p.join('&') : '');
  }
  function get(q) {
    return pm.fetch(url(q), { headers: { 'X-Requested-With': 'fetch' }, cache: 'no-store' }).then(function (r) {
      if (!r.ok) throw new Error(String(r.status));
      var now = parseFloat(r.headers.get('X-Now'));
      if (now) skew = now - Date.now() / 1000;
      return r.text().then(function (h) {
        return { body: parse(h), rev: parseInt(r.headers.get('X-Rev'), 10) || 0, more: r.headers.get('X-More') === '1',
                 reset: r.headers.get('X-Reset') === '1' };
      });
    });
  }

  // ---- placing items ----
  function isOb(n) { return !!(n.classList && n.classList.contains('ci-ob')); }
  function shape(box) { return kids(box).map(function (c) { return { seq: seqOf(c), ob: isOb(c) }; }); }
  function insertBySeq(box, n) {
    var k = kids(box);
    box.insertBefore(n, k[rules.slot(shape(box), seqOf(n))] || null);
  }
  // a user item with data-ob takes its bubble's place (same spot when only bubbles lie between), else
  // goes to its seq position and the bubble is dropped
  function insertUser(box, n, oid) {
    var b = bubble(oid), k = kids(box), sh = shape(box), i = rules.slot(sh, seqOf(n)), j = b ? k.indexOf(b) : -1;
    if (j >= 0 && rules.replaces(sh, i, j)) { b.replaceWith(n); return; }
    box.insertBefore(n, k[i] || null);
    if (b) b.remove();
  }
  function bubble(oid) { var b = oid && document.getElementById('ob-' + oid); return b && isOb(b) ? b : null; }
  function linked(oid) { return !!(oid && /^\d+$/.test(oid) && root.querySelector('.ci-items .ci-user:not(.ci-ob)[data-ob="' + oid + '"]')); }
  function dropPending(oid) { var b = bubble(oid); if (b) b.remove(); }
  // a delivered bubble: once to the end of the conversation (before the first page: stays put)
  function toFlow(n) {
    var l = list();
    if (!l) { if (!n.parentNode) { var h = pending(); if (h) h.appendChild(n); } return; }
    var empty = root.querySelector('.ci-empty');
    if (empty) empty.remove();
    l.appendChild(n);
  }
  function putOb(n) {
    var cur = document.getElementById(n.id), host = pending();
    var at = !cur ? 'none' : (host && host.contains(cur) ? 'pending' : 'flow');
    var to = rules.obTarget(n.hasAttribute('data-flow'), at), node = cur || n;
    if (cur) swapState(cur, n);
    if (to === 'flow') toFlow(node);
    else if (to === 'pending' && host) host.appendChild(node);
    if (!cur || to !== 'stay') settle(node);
  }
  // a user bubble: only its delivery state and actions change
  function swapState(cur, n) {
    ['.ob-st', '.ob-act'].forEach(function (sel) {
      var a = cur.querySelector(sel), b = n.querySelector(sel);
      if (a && b) a.replaceWith(b);
      else if (a) a.remove();
      else if (b && sel === '.ob-act') cur.appendChild(b);
      else if (b) { var h = cur.querySelector('.chat-meta'); if (h) h.insertBefore(b, h.firstChild); }
    });
    if (n.hasAttribute('data-flow')) cur.setAttribute('data-flow', ''); else cur.removeAttribute('data-flow');
    if (n.hasAttribute('data-state')) {
      cur.className = n.className;
      cur.setAttribute('data-state', n.getAttribute('data-state'));
      cur.setAttribute('aria-label', n.getAttribute('aria-label') || '');
    }
  }
  // the header of a tool group, from its rows: total, one chip per tool name, running count
  function head(g) {
    var rows = g.querySelectorAll('.ci-rows > li'), counts = {}, fam = {}, order = [], run = 0;
    Array.prototype.forEach.call(rows, function (li) {
      var n = li.getAttribute('data-name') || 'tool';
      if (!counts[n]) { counts[n] = 0; order.push(n); fam[n] = li.getAttribute('data-fam') || 'other'; }
      counts[n]++;
      if (li.getAttribute('data-status') === 'run') run++;
    });
    var tot = g.querySelector('.ci-tot'), chips = g.querySelector('.ci-chips'), rn = g.querySelector('.ci-run');
    if (tot) {
      tot.textContent = '';
      tot.appendChild(el('b', null, String(rows.length)));
      tot.appendChild(document.createTextNode(' tool call' + (rows.length === 1 ? '' : 's')));
    }
    if (chips) {
      chips.textContent = '';
      order.forEach(function (n) {
        var c = el('span', 'tchip tf-' + fam[n], n === 'Agent' ? 'Sub-Agent' : n);
        c.appendChild(document.createTextNode(' '));
        c.appendChild(el('b', null, String(counts[n])));
        chips.appendChild(c);
      });
    }
    if (rn) {
      rn.textContent = '';
      if (run && root.hasAttribute('data-ended')) {  // agent gone, not reconciled yet (itemsync._bg_reconcile): not running
        rn.appendChild(el('span', 'ci-ts is-bg', run + ' no end seen'));
      } else if (run) {
        var sp = el('span', 'ci-spin');
        sp.setAttribute('aria-hidden', 'true');
        rn.appendChild(sp);
        rn.appendChild(document.createTextNode(run + ' running'));
      }
    }
  }
  // an item that is already on the page: tool rows replaced one by one, user state swapped; text never changes
  function update(cur, n) {
    if (n.matches('details.ci-tg')) {
      var rows = cur.querySelector('.ci-rows');
      kids(n.querySelector('.ci-rows')).forEach(function (li) {
        var old = document.getElementById(li.id);
        if (old) old.replaceWith(li); else if (rows) insertBySeq(rows, li);
      });
      head(cur);
      return cur;
    }
    if (n.matches('article.ci-user')) {
      swapState(cur, n);
      var oid = n.getAttribute('data-ob');
      if (oid) { cur.setAttribute('data-ob', oid); dropPending(oid); }
      return cur;
    }
    return null;
  }
  function place(box, n) {
    var cur = n.id && document.getElementById(n.id);
    if (cur && box.contains(cur)) return update(cur, n);
    if (oldest && seqOf(n) < oldest) return null;  // older than the loaded window: Load older brings it
    if (n.matches('details.ci-tg') && opened[n.id]) n.open = true;
    var oid = n.getAttribute('data-ob');
    if (oid && n.matches('article.ci-user')) insertUser(box, n, oid); else insertBySeq(box, n);
    return n;
  }
  function markOldest() { var l = list(); oldest = l && l.firstElementChild ? seqOf(l.firstElementChild) : 0; }

  // outbox bubbles: the response is the full list of open ones (authoritative), minus those already linked
  function mergeOutbox(box) {
    var host = pending();
    if (!host || !box) return;
    var seen = {};
    kids(box).forEach(function (n) {
      seen[n.id] = true;
      var oid = n.getAttribute('data-ob');
      if (linked(oid)) { dropPending(oid); return; }
      putOb(n);
    });
    kids(host).forEach(function (n) {
      var fresh = Date.now() - (parseInt(n.getAttribute('data-fresh'), 10) || 0) < FRESH_MS;
      if (n.id && !seen[n.id] && !fresh) n.remove();
    });
  }

  // ---- loads ----
  function full() {
    return get('').then(function (res) {
      var b = res.body, obs = b.querySelector('.ci-obs');
      root.textContent = '';
      kids(b).forEach(function (n) { if (n !== obs) root.appendChild(n); });
      cursor = res.rev;
      markOldest();
      mergeOutbox(obs);
      settle(root);
      tick();
      scroll('bottom');
      if (res.more) again = true;
      soon();
    });
  }
  // an empty first page (the feed may not have ingested this agent yet; ended pages poll only every
  // 15 s): ask again shortly, a few times
  function soon() {
    var l = list();
    if ((l && l.firstElementChild) || quick >= QUICK_N) return;
    quick++;
    setTimeout(function () { if (!document.hidden) refresh(); }, QUICK_MS);
  }
  // lowest and highest item seq on the page: the server answers X-Reset when one of them is gone
  function have() {
    var b = list(), lo = 0, hi = 0;
    if (b) b.querySelectorAll('[id^="i-"][data-seq]').forEach(function (n) {
      var s = seqOf(n);
      if (s && (!lo || s < lo)) lo = s;
      if (s > hi) hi = s;
    });
    return lo ? '&lo=' + lo + '&hi=' + hi : '';
  }
  function poll() {
    return get('after_rev=' + cursor + have()).then(function (res) {
      var box = list();
      if (!box || res.reset) { cursor = null; return full(); }
      var its = res.body.querySelector('.ci-items'), n = 0;
      kids(its).forEach(function (x) { if (place(box, x)) n++; });
      mergeOutbox(res.body.querySelector('.ci-obs'));
      if (res.rev > cursor) cursor = res.rev;
      if (n) {
        var empty = root.querySelector('.ci-empty');
        if (empty) empty.remove();
        if (!oldest) markOldest();
        settle(box);
      }
      tick();
      scroll('follow');
      if (res.more) again = true;
      soon();
    });
  }
  function refresh() {
    if (inflight) { again = true; return; }
    inflight = true;
    lastFetch = Date.now();
    (cursor === null ? full() : poll()).catch(function () { /* next poll retries */ }).then(function () {
      inflight = false;
      if (again) { again = false; refresh(); }
    });
  }
  function older(btn) {
    btn.disabled = true;
    get('before=' + encodeURIComponent(btn.getAttribute('data-ci-older'))).then(function (res) {
      var box = list(), its = res.body.querySelector('.ci-items'), more = res.body.querySelector('.ci-more');
      if (!box) return;
      scroll('keep', box.firstElementChild);
      kids(its).reverse().forEach(function (n) {
        var cur = n.id && document.getElementById(n.id);
        if (cur) update(cur, n); else box.prepend(n);
      });
      var old = root.querySelector('.ci-more');
      if (old && more) old.replaceWith(more);
      markOldest();
      settle(box);
      tick();
      scroll('restore');
    }).catch(function () { btn.disabled = false; });
  }

  // ---- timers: elapsed from data-start, "waiting" after WAIT_S ----
  function pad(n) { return (n < 10 ? '0' : '') + n; }
  function fmt(s) {
    s = Math.max(0, Math.floor(s));
    if (s < 60) return s + 's';
    if (s < 3600) return Math.floor(s / 60) + 'm ' + pad(s % 60) + 's';
    return Math.floor(s / 3600) + 'h ' + pad(Math.floor(s / 60) % 60) + 'm';
  }
  function tick() {
    var now = Date.now() / 1000 + skew;
    document.querySelectorAll('#ci [data-ago], #ci-pending [data-ago]').forEach(function (t) {
      var d = now - parseFloat(t.getAttribute('data-ago')), e = t.firstElementChild || t;
      if (isFinite(d)) e.textContent = rules.ago(d);
    });
    document.querySelectorAll('#ci [data-start], #chat [data-start]').forEach(function (t) {
      var d = now - parseFloat(t.getAttribute('data-start')), e = t.querySelector('.el') || t;
      if (!isFinite(d)) return;
      e.textContent = fmt(d);
      if (t.classList.contains('ci-ts') && !t.classList.contains('is-bg')) t.classList.toggle('is-wait', d > WAIT_S);
    });
  }

  // ---- outbox actions ----
  var VERB = { now: 'send', edit: 'edit', cancel: 'cancel', retry: 'retry' };
  function toComposer(text) {
    var ta = document.querySelector('[data-composer][data-agent] [data-cmp-input]');
    if (!ta || !text) return;
    ta.value = ta.value.trim() ? ta.value.replace(/\s+$/, '') + '\n' + text : text;
    ta.dispatchEvent(new Event('input', { bubbles: true }));
    ta.focus();
  }
  function post(art, oid, act, btn) {
    btn.disabled = true;
    return pm.fetch(BASE + '/outbox/' + oid + '/' + act, {
      method: 'POST', body: new URLSearchParams(), csrf: 'both', headers: { 'X-Requested-With': 'fetch' }
    }).then(function (r) {
      if (!r.ok) throw new Error(String(r.status));
      if (act === 'edit') return r.json().then(function (j) { toComposer(j && j.text); art.remove(); });
      if (act === 'cancel') { art.remove(); return null; }
      return r.text().then(function (h) { var n = parse(h).querySelector('article[data-ob]'); if (n) swapState(art, n); });
    }).catch(function () {
      btn.disabled = false;
      flash('Could not ' + VERB[act] + ' this message. Try again.');
    }).then(refresh);
  }
  function obAct(btn) {
    var art = btn.closest('[data-ob]'), oid = art && art.getAttribute('data-ob'), act = btn.getAttribute('data-ob-act');
    if (!oid || !/^\d+$/.test(oid) || !VERB[act]) return;
    if (act !== 'cancel') { post(art, oid, act, btn); return; }
    if (!window.pmConfirm) return;
    window.pmConfirm({ title: 'Cancel this message?', body: 'It is not sent to the agent.',
                       confirmLabel: 'Cancel message', cancelLabel: 'Keep it', danger: true })
      .then(function (ok) { if (ok) post(art, oid, act, btn); });
  }

  // ---- wiring ----
  document.addEventListener('click', function (e) {
    var t = e.target;
    var b = t.closest && t.closest('[data-ci-older]');
    if (b && root.contains(b)) { e.preventDefault(); older(b); return; }
    var a = t.closest && t.closest('[data-ob-act]');
    if (a) { e.preventDefault(); obAct(a); }
  });
  document.addEventListener('toggle', function (e) {
    var d = e.target;
    if (!d || !d.matches || !d.id) return;
    if (d.matches('details.ci-cnt')) opened[d.id] = d.open;
    else if (d.matches('details.ci-tg') && d.closest('.ci-cnt')) { if (!d.open) d.open = true; }  // forced open inside a total
    else if (d.matches('details.ci-tg')) opened[d.id] = d.open;
  }, true);
  document.addEventListener('pm:chat-view', function () { settle(list()); });
  function mine(e) { var d = e.detail; return !d || !d.agent_id || d.agent_id === AID; }
  document.addEventListener('pm:chat_items', function (e) { if (mine(e)) refresh(); });
  document.addEventListener('pm:chat_outbox', function (e) { if (mine(e)) refresh(); });
  document.addEventListener('pm:live-open', refresh);
  document.addEventListener('visibilitychange', function () { if (!document.hidden) refresh(); });
  setInterval(function () {
    if (!document.hidden && Date.now() - lastFetch >= POLL_MS - 200) refresh();
  }, POLL_MS);
  setInterval(function () { if (!document.hidden) tick(); }, 1000);
  document.addEventListener('htmx:afterSettle', function (e) { if (e.target && e.target.id === 'chat') tick(); });

  pm.chatItems = {
    // the message POST answers with the outbox bubble (_outbox.html): show it below the running turn
    addOutbox: function (html) {
      var n = parse(html).querySelector('article[data-ob]'), host = pending();
      if (!n || !host) return false;
      n.setAttribute('data-fresh', String(Date.now()));
      if (!linked(n.getAttribute('data-ob'))) putOb(n);
      scroll('bottom');
      refresh();
      return true;
    },
    refresh: refresh
  };
  refresh();
})();
