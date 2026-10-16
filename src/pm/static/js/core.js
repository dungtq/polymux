/* pm core helpers: window.pm.csrf / fetch / esc / debounce / notify / badges / store / openset / copy. Loaded first among the deferred scripts in
 * base.html, so every later deferred script may call them at load time. External file only (strict CSP).
 *
 * Document click handlers stay where they are, one per feature file. Do NOT merge them into one
 * delegated handler here: some listen in the capture phase (rowx.js, dialog.js, newchat.js) and others
 * call stopPropagation/preventDefault, so their order and phase are part of the behaviour.
 *
 * pm.fetch(url, opts): window.fetch with credentials 'same-origin' unless opts.credentials is set; every
 * other option (method, body, headers, signal, keepalive, cache, ...) passes through unchanged. The CSRF
 * token is only added on request, so each call site keeps its own transport:
 *   opts.csrf = 'header' -> X-CSRF-Token header
 *   opts.csrf = 'body'   -> `csrf` field set on a URLSearchParams / FormData body
 *   opts.csrf = 'both'   -> both of the above
 * String bodies are never rewritten (put csrf: pm.csrf() into the params yourself). */
(function () {
  'use strict';
  var pm = window.pm = window.pm || {};

  pm.csrf = function () {
    var m = document.querySelector('meta[name="csrf-token"]');
    if (m) return m.content || '';
    var i = document.querySelector('input[name="csrf"]');
    return i ? i.value : '';
  };

  function withHeader(h, tok) {
    if (typeof Headers !== 'undefined' && h instanceof Headers) {
      var hh = new Headers(h); hh.set('X-CSRF-Token', tok); return hh;
    }
    var o = {};
    if (h) Object.keys(h).forEach(function (k) { o[k] = h[k]; });
    o['X-CSRF-Token'] = tok;
    return o;
  }

  pm.fetch = function (url, opts) {
    var o = {}, mode = opts && opts.csrf;
    if (opts) Object.keys(opts).forEach(function (k) { if (k !== 'csrf') o[k] = opts[k]; });
    if (o.credentials === undefined) o.credentials = 'same-origin';
    if (mode === 'header' || mode === 'both') o.headers = withHeader(o.headers, pm.csrf());
    if ((mode === 'body' || mode === 'both') && o.body && typeof o.body.set === 'function') o.body.set('csrf', pm.csrf());
    return window.fetch(url, o);
  };

  /* recap box (.n-recap / .ntfy-recap, data-recap = /a/<id>/recap-row): fetch the partial once, on first open
     (agent tables: rowx.js, ntfy panel: ntfy.js) */
  pm.loadRecap = function (box) {
    if (box.getAttribute('data-loaded')) return;
    box.setAttribute('data-loaded', '1');
    pm.fetch(box.getAttribute('data-recap'), { credentials: 'same-origin', headers: { 'X-Requested-With': 'fetch' } })
      .then(function (r) { if (!r.ok) throw new Error(r.status); return r.text(); })
      .then(function (h) { box.innerHTML = h; })
      .catch(function () { box.removeAttribute('data-loaded'); box.textContent = 'Recap unavailable.'; });
  };

  var ESC = { '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' };
  pm.esc = function (t) {
    return String(t).replace(/[&<>"']/g, function (c) { return ESC[c]; });
  };

  pm.debounce = function (fn, ms) {
    var t = null;
    return function () {
      var self = this, args = arguments;
      if (t) clearTimeout(t);
      t = setTimeout(function () { t = null; fn.apply(self, args); }, ms);
    };
  };

  /* Notices: one <p class="flash ok|err"> in a flash slot (#flash, the composer's .flash-inline, [data-cmp-flash]).
   * pm.notify(msg, {kind: 'ok'|'err', ttl, html, target}) replaces the slot's content (opts.target, else #flash):
   * text by default (escaped); html: true inserts msg as is (a server flash fragment, e.g. a fetch answer).
   * An ok notice clears after ttl ms (default 4000, 0 = keep); an err notice stays until replaced. The same
   * auto-hide runs after every htmx swap into a flash slot. A notice with a link, button or field (fork / launch
   * "open" links, "File attached.") is kept: hiding it would take an action away. Returns the slot or null. */
  var NOTE_TTL = 4000, SLOTS = '#flash, .flash-inline, [data-cmp-flash]';
  var KEEP = 'a, button, input, select, textarea, form, [data-image-path]';
  function hideLater(box, ttl) {
    var p = box.querySelector('.flash.ok');
    if (!p || !(ttl > 0) || box.querySelector('.flash.err') || box.querySelector(KEEP)) return;
    setTimeout(function () { if (p.parentNode && box.contains(p)) box.textContent = ''; }, ttl);  // still this notice
  }
  pm.notify = function (msg, opts) {
    var o = opts || {}, box = o.target || document.getElementById('flash');
    if (!box) return null;
    var kind = o.kind === 'err' ? 'err' : 'ok';
    box.innerHTML = o.html ? String(msg) : '<p class="flash ' + kind + '">' + pm.esc(msg) + '</p>';
    hideLater(box, o.ttl === undefined ? NOTE_TTL : o.ttl);
    return box;
  };
  if (typeof document.addEventListener === 'function') {
    document.addEventListener('htmx:afterSwap', function (e) {  // fired on each swapped element (the slot, for innerHTML)
      var t = e.target;
      if (t && t.matches && t.matches(SLOTS)) hideLater(t, NOTE_TTL);
    });
  }

  /* Count badges (count_pill in _macros.html, the Inbox tab, the bell, the sidebar unread pills):
   * pm.badges.text(n) = "99+" over 99, "" at 0 (same rule as count_pill); pm.badges.set(el | NodeList, n) writes it and
   * hides the badge at 0. pm.badges.on(fn) is the one refresh subscriber: every fn runs (once, 400 ms after the last
   * trigger) after an agent / inbox event or an /inbox/ htmx request, when the tab is shown again, and every minute.
   * Its document listeners are added by the first on(), never at load. */
  var subs = [], subT = null, BADGE_EVENTS = ['pm:agent_state', 'pm:agent_added', 'pm:agent_removed', 'pm:inbox', 'pm:refresh'];
  function runSubs() { subT = null; subs.slice().forEach(function (f) { try { f(); } catch (e) { /* one bad subscriber must not stop the rest */ } }); }
  function badgesSoon() { if (!subT) subT = setTimeout(runSubs, 400); }
  function listen() {
    BADGE_EVENTS.forEach(function (t) { document.addEventListener(t, badgesSoon); });
    document.addEventListener('htmx:afterRequest', function (e) {
      var p = e.detail && e.detail.requestConfig && e.detail.requestConfig.path;
      if (p && p.indexOf('/inbox/') === 0 && p !== '/inbox/unread') badgesSoon();
    });
    document.addEventListener('visibilitychange', function () { if (!document.hidden) badgesSoon(); });
    setInterval(function () { if (!document.hidden) runSubs(); }, 60000);
  }
  pm.badges = {
    text: function (n) { n = Number(n) || 0; return n > 99 ? '99+' : n > 0 ? String(n) : ''; },
    set: function (els, n) {
      var t = pm.badges.text(n), list = els && els.nodeType === 1 ? [els] : (els || []);
      Array.prototype.forEach.call(list, function (b) { b.textContent = t; b.hidden = !t; });
    },
    on: function (fn) {
      if (typeof fn !== 'function') return;
      if (!subs.length) listen();
      subs.push(fn);
    },
    soon: badgesSoon
  };

  /* pm.store: localStorage that never throws (private mode, quota, blocked storage). get(key, dflt) returns dflt
   * (default null) when the key is missing or storage fails; set/remove return false on failure. getJSON/setJSON
   * (de)serialise; a value that does not parse reads as dflt. */
  function dflt(d) { return d === undefined ? null : d; }
  pm.store = {
    get: function (k, d) {
      try { var v = window.localStorage.getItem(k); return v === null ? dflt(d) : v; } catch (e) { return dflt(d); }
    },
    set: function (k, v) {
      try { window.localStorage.setItem(k, String(v)); return true; } catch (e) { return false; }
    },
    remove: function (k) {
      try { window.localStorage.removeItem(k); return true; } catch (e) { return false; }
    },
    getJSON: function (k, d) {
      var v = pm.store.get(k, null);
      if (v === null) return dflt(d);
      try { return JSON.parse(v); } catch (e) { return dflt(d); }
    },
    setJSON: function (k, v) {
      var s;
      try { s = JSON.stringify(v); } catch (e) { return false; }
      return pm.store.set(k, s);
    }
  };

  /* pm.openset(key): the set of open rows / groups of one list, kept in pm.store under key as {"<id>": 1, ...} (the
   * format rowx.js, srow.js and backlog.js always used, so saved state survives). Read once on creation; has(id),
   * set(id, on) (saves, returns on), toggle(id) (saves, returns the new state). Only the store is shared: each feature
   * keeps its own document handlers (see the click-handler rule above). */
  pm.openset = function (key) {
    var o = pm.store.getJSON(key, {});
    if (!o || typeof o !== 'object' || Array.isArray(o)) o = {};
    var api = {
      has: function (id) { return !!o[id]; },
      set: function (id, on) {
        if (on) o[id] = 1; else delete o[id];
        pm.store.setJSON(key, o);
        return !!on;
      },
      toggle: function (id) { return api.set(id, !o[id]); }
    };
    return api;
  };

  /* pm.copy(text) -> Promise<bool>: the Clipboard API in a secure context; else, or when it refuses, a hidden textarea
   * + execCommand('copy') (16px font so iOS does not zoom; focus goes back to where it was). Never rejects. */
  function legacyCopy(text) {
    var d = document, prev = d.activeElement, t = d.createElement('textarea'), ok = false;
    t.value = text;
    t.setAttribute('readonly', '');
    t.setAttribute('aria-hidden', 'true');
    t.className = 'copy-scratch';
    t.style.cssText = 'position:fixed;top:0;left:-9999px;opacity:0;font-size:16px';
    d.body.appendChild(t);
    try {
      t.focus({ preventScroll: true });
      t.select();
      t.setSelectionRange(0, text.length);
      ok = !!d.execCommand('copy');
    } catch (err) { ok = false; }
    d.body.removeChild(t);
    try { if (prev && prev.focus) prev.focus({ preventScroll: true }); } catch (err) { /* focus is best effort */ }
    return ok;
  }
  pm.copy = function (text) {
    var s = text == null ? '' : String(text), nav = window.navigator, c = nav && nav.clipboard;
    if (c && c.writeText && window.isSecureContext) {
      return c.writeText(s).then(function () { return true; }, function () { return legacyCopy(s); });
    }
    return Promise.resolve(legacyCopy(s));
  };
})();
