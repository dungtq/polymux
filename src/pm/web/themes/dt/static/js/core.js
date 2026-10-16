/* dt theme (pm v7) core helpers. Owns window.pm7 and these helpers on it:
     pm7.fetch(url, {method, form | json, csrf, headers, keepalive, signal, cache}) -> Promise<Response>
     pm7.why(response | null, what) -> Promise<text>, pm7.fail(what, response | null): a failed answer's message
     pm7.reply(response | null) -> Promise<{ok, notice, redirect, json}>: a POST's outcome (a JSON {ok: false} is a
       failure even with status 200: the core's Settings refusals)
     pm7.esc(text), pm7.debounce(fn, ms), pm7.store.get/set/remove(name) (keys pm.v7.<area>.<name>),
     pm7.isApple, pm7.on(type, fn) -> off(), pm7.emit(type, detail), pm7.ready(fn), pm7.$ / pm7.$$,
     pm7.isField(el), pm7.phone() (< 700 px), pm7.overlay() (< 900 px: sidebar and aside overlay), pm7.icon(name),
     pm7.el(spec, attrs | cls, ...kids) -> element, pm7.add(node, kids): the one DOM builder (PARTS.md §10).
   Loaded first among the deferred shell scripts (only js/head.js runs earlier, blocking). The ONLY dt file that
   calls fetch( or reads the CSRF meta (archcheck js-fetch / js-csrf); every other file goes through pm7.fetch.
   Hooks: meta[name=csrf-token]; the icon sprite path is taken from the first <svg><use href="...#id"> on the page.
   pm7.ready(fn) runs fn on DOMContentLoaded, i.e. after every deferred shell script has loaded, so files may call
   each other's pm7.* there without caring about script order. */
(function () {
  'use strict';
  const d = document;
  const pm7 = window.pm7 = window.pm7 || {};

  /* ---------- fetch with same-origin credentials and the CSRF token ---------- */
  function token() {
    const m = d.querySelector('meta[name="csrf-token"]');
    return m ? m.content || '' : '';
  }
  function isParams(x) { return x && typeof x.append === 'function' && typeof x.set === 'function'; }

  /* method defaults to POST when a body is given; csrf defaults to true for every non-GET request and adds the
     X-CSRF-Token header (plus a `csrf` field on a form body: the dt routes accept either). form = plain object,
     URLSearchParams or FormData; json = any JSON value. GET requests skip the HTTP cache (fragments use ETags). */
  pm7.fetch = function (url, opts) {
    const o = opts || {};
    const hasBody = o.form != null || o.json !== undefined;
    const method = String(o.method || (hasBody ? 'POST' : 'GET')).toUpperCase();
    const write = method !== 'GET' && method !== 'HEAD';
    const csrf = o.csrf === undefined ? write : !!o.csrf;
    const headers = { 'X-Requested-With': 'fetch' };
    if (o.headers) Object.keys(o.headers).forEach((k) => { headers[k] = o.headers[k]; });
    const init = { method: method, credentials: 'same-origin', headers: headers };
    init.cache = o.cache || (write ? 'default' : 'no-store');
    if (o.form != null) {
      let body = o.form;
      if (!isParams(body)) {
        body = new URLSearchParams();
        Object.keys(o.form).forEach((k) => { body.append(k, o.form[k] == null ? '' : String(o.form[k])); });
      }
      if (csrf) body.set('csrf', token());
      init.body = body;
    } else if (o.json !== undefined) {
      init.body = JSON.stringify(o.json);
      headers['Content-Type'] = 'application/json';
    }
    if (csrf) headers['X-CSRF-Token'] = token();
    if (o.keepalive) init.keepalive = true;
    if (o.signal) init.signal = o.signal;
    return window.fetch(url, init);
  };

  /* ---------- the server's message of a failed answer, for an error toast (V7-CODE-5, DESIGN §6.21) ----------
     JSON {"detail" | "error" | "msg" | "message" | "notice": text} (FastAPI HTTPException; a validation list: its
     first msg) or a text/plain body. One line, at most 160 characters, long token-like runs masked; HTML pages and
     empty bodies give no message. pm7.why(r, what) -> "<what>: <message>", else "<what> (<status>)", "<what>: no
     answer" without r. pm7.fail(what, r) toasts that as an error. The body is read from a clone: the caller may
     still read r. */
  const TOKENISH = /[A-Za-z0-9+/_=-]{32,}/g;
  function tidy(t) {
    t = String(t == null ? '' : t).replace(/[\u0000-\u001f\u007f\s]+/g, ' ').trim().replace(TOKENISH, '…');
    return t.length > 160 ? t.slice(0, 159) + '…' : t;
  }
  async function message(r) {
    const ct = (r.headers && r.headers.get('Content-Type')) || '';
    if (!/json|^text\/plain/i.test(ct)) return '';
    let body = '';
    try { body = await r.clone().text(); } catch (e) { return ''; }
    if (!/json/i.test(ct)) return tidy(body);
    try {
      const j = JSON.parse(body);
      let m = j && typeof j === 'object' && !Array.isArray(j)
        ? j.detail || j.error || j.msg || j.message || j.notice : j;
      if (Array.isArray(m)) m = m[0] && typeof m[0] === 'object' ? m[0].msg : m[0];
      return typeof m === 'string' ? tidy(m) : '';
    } catch (e) { return ''; }
  }
  pm7.why = async function (r, what) {
    const w = what || 'Failed';
    if (!r) return w + ': no answer';
    const m = await message(r);
    return m ? w + ': ' + m : w + ' (' + r.status + ')';
  };
  /* {ok, notice, redirect, json}: ok = HTTP 2xx and not a JSON body saying ok: false; notice (tidied) and redirect
     (a path; the caller checks it is same-origin) from a JSON body. Read from a clone: the caller may still read r. */
  pm7.reply = async function (r) {
    let j = null;
    if (r && /json/i.test((r.headers && r.headers.get('Content-Type')) || '')) {
      try { j = await r.clone().json(); } catch (e) { j = null; }
    }
    const o = j && typeof j === 'object' && !Array.isArray(j) ? j : null;
    return { ok: !!r && r.ok && !(o && o.ok === false), notice: o && typeof o.notice === 'string' ? tidy(o.notice) : '',
      redirect: o && typeof o.redirect === 'string' ? o.redirect : '', json: o };
  };
  pm7.fail = function (what, r) {
    return pm7.why(r, what).then((t) => { if (pm7.toast) pm7.toast(t, '', { error: true }); return t; });
  };

  /* ---------- text, timing, storage ---------- */
  const ESC = { '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' };
  pm7.esc = function (t) { return String(t == null ? '' : t).replace(/[&<>"']/g, (c) => ESC[c]); };

  pm7.debounce = function (fn, ms) {
    let t = 0;
    return function () {
      const self = this, args = arguments;
      clearTimeout(t);
      t = setTimeout(() => { t = 0; fn.apply(self, args); }, ms);
    };
  };

  /* localStorage that never throws (private mode, quota, blocked storage); names get the pm.v7. prefix */
  const PREFIX = 'pm.v7.';
  function key(name) { name = String(name); return name.indexOf(PREFIX) === 0 ? name : PREFIX + name; }
  pm7.store = {
    get(name, dflt) {
      try {
        const v = window.localStorage.getItem(key(name));
        return v === null ? (dflt === undefined ? null : dflt) : v;
      } catch (e) { return dflt === undefined ? null : dflt; }
    },
    set(name, value) {
      try { window.localStorage.setItem(key(name), String(value)); return true; } catch (e) { return false; }
    },
    remove(name) {
      try { window.localStorage.removeItem(key(name)); return true; } catch (e) { return false; }
    }
  };

  /* ---------- platform, events, DOM ---------- */
  const nav = window.navigator || {};
  const plat = (nav.userAgentData && nav.userAgentData.platform) || nav.platform || nav.userAgent || '';
  pm7.isApple = /Mac|iPhone|iPad|iPod/i.test(plat);  // iPadOS reports MacIntel

  pm7.on = function (type, fn) {
    d.addEventListener(type, fn);
    return () => d.removeEventListener(type, fn);
  };
  pm7.emit = function (type, detail) {
    d.dispatchEvent(new CustomEvent(type, { detail: detail === undefined ? null : detail }));
  };

  let loaded = d.readyState === 'complete';
  const queue = [];
  if (!loaded) {
    d.addEventListener('DOMContentLoaded', () => {
      loaded = true;
      queue.splice(0).forEach((fn) => { try { fn(); } catch (e) { setTimeout(() => { throw e; }); } });
    });
  }
  pm7.ready = function (fn) { if (loaded) fn(); else queue.push(fn); };

  pm7.$ = function (sel, root) { return (root || d).querySelector(sel); };
  pm7.$$ = function (sel, root) { return Array.from((root || d).querySelectorAll(sel)); };

  const NOT_TEXT = /^(button|checkbox|radio|submit|reset|image|file|range|color|hidden)$/i;
  pm7.isField = function (el) {
    if (!el || el.nodeType !== 1) return false;
    if (el.isContentEditable) return true;
    const n = el.tagName;
    if (n === 'TEXTAREA' || n === 'SELECT') return true;
    return n === 'INPUT' && !NOT_TEXT.test(el.type || 'text');
  };
  pm7.phone = function () { return window.innerWidth < 700; };
  pm7.overlay = function () { return window.innerWidth < 900; };

  /* the one DOM builder (no HTML strings): spec "tag.cls.cls"; attrs = an object (null / false values skipped,
     true = "", cls adds classes, on = {event: fn}) or a string = the class name(s); kids: nodes, text (a text node,
     never parsed), arrays; null / false skipped. pm7.add(node, kids) appends kids the same way. */
  function add(n, kid) {
    if (kid == null || kid === false) return n;
    if (Array.isArray(kid)) { kid.forEach((k) => add(n, k)); return n; }
    n.appendChild(kid.nodeType ? kid : d.createTextNode(String(kid)));
    return n;
  }
  pm7.add = add;
  pm7.el = function (spec, attrs, ...kids) {
    const parts = String(spec).split('.');
    const n = d.createElement(parts[0] || 'div');
    if (parts.length > 1) n.className = parts.slice(1).join(' ');
    const a = typeof attrs === 'string' ? { cls: attrs } : attrs || {};
    Object.keys(a).forEach((k) => {
      const v = a[k];
      if (v == null || v === false) return;
      if (k === 'cls') { String(v).split(/\s+/).filter(Boolean).forEach((c) => n.classList.add(c)); return; }
      if (k === 'on') { Object.keys(v).forEach((ev) => n.addEventListener(ev, v[ev])); return; }
      n.setAttribute(k, v === true ? '' : String(v));
    });
    return add(n, kids);
  };

  /* <svg class="i"><use href="<sprite>#name"></svg>, the sprite path read from the page's own icons */
  let sprite = null;
  pm7.icon = function (name, cls) {
    if (sprite === null) {
      const u = d.querySelector('svg use[href*="#"]');
      sprite = u ? u.getAttribute('href').split('#')[0] : '';
    }
    if (!sprite || !name) return null;
    const NS = 'http://www.w3.org/2000/svg';
    const svg = d.createElementNS(NS, 'svg'), use = d.createElementNS(NS, 'use');
    svg.setAttribute('class', cls || 'i');
    svg.setAttribute('viewBox', '0 0 24 24');
    svg.setAttribute('aria-hidden', 'true');
    use.setAttribute('href', sprite + '#' + name);
    svg.appendChild(use);
    return svg;
  };
})();
