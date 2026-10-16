/* Builder page: draggable divider, source (static files | URL), reload, path field, files list, auto reload on file change,
 * allowed external URLs (data-frame-hosts), the "Preview: <target>" line of the agent's newest reply and chat
 * links that open in the aside.
 * Static file (CSP script-src 'self'): no inline script, no eval, no native popups. */
(function () {
  'use strict';
  var root = document.getElementById('builder');
  if (!root) return;
  var $ = function (id) { return document.getElementById(id); };
  var base = root.dataset.base || '';
  var frame = $('builder-frame'), stage = $('bld-stage'), pathIn = $('bld-path'), openA = $('bld-open');
  var empty = $('bld-empty'), stDot = $('bld-stdot'), stTx = $('bld-sttx');
  var split = root.querySelector('.bld-split'), div = root.querySelector('.bld-div');
  var cur = (root.dataset.entry || '');
  var hosts = (root.dataset.frameHosts || '').split(/\s+/).filter(Boolean);
  var ext = null;  // the allowed external URL on show, else null (then the mtime poll does not reload)
  var lastExt = null, srcSel = $('bld-src');
  var UK = 'pm-builder-url:' + (root.dataset.agent || '');
  var ENTRIES = ['index.html', 'public/index.html', 'dist/index.html', 'build/index.html', 'out/index.html'];

  function store(k, v) { try { if (v === null) localStorage.removeItem(k); else localStorage.setItem(k, v); } catch (e) { /* private mode */ } }
  function load(k) { try { return localStorage.getItem(k); } catch (e) { return null; } }
  (window.pm = window.pm || {}).builder = { load: load, store: store };  // builder-screen.js (screen size, zoom) shares them

  /* ---- divider */
  var LK = 'pm-builder-left';
  function setLeft(p, save) {
    p = Math.max(20, Math.min(70, Math.round(p * 10) / 10));
    split.style.setProperty('--bld-left', p + '%');
    div.setAttribute('aria-valuenow', String(Math.round(p)));
    if (save) store(LK, String(p));
  }
  var saved = parseFloat(load(LK));
  if (saved >= 20 && saved <= 70) setLeft(saved, false);
  div.addEventListener('pointerdown', function (e) {
    if (e.button !== 0) return;
    e.preventDefault();
    try { div.setPointerCapture(e.pointerId); } catch (x) { /* ignore */ }
    var r = split.getBoundingClientRect(), last = null;
    function move(ev) { last = (ev.clientX - r.left) / r.width * 100; setLeft(last, false); }
    function up() {
      div.removeEventListener('pointermove', move);
      div.removeEventListener('pointerup', up);
      div.removeEventListener('pointercancel', up);
      if (last !== null) setLeft(last, true);
    }
    div.addEventListener('pointermove', move);
    div.addEventListener('pointerup', up);
    div.addEventListener('pointercancel', up);
  });
  div.addEventListener('keydown', function (e) {
    var now = parseFloat(div.getAttribute('aria-valuenow')) || 38, d = 0;
    if (e.key === 'ArrowLeft') d = -2; else if (e.key === 'ArrowRight') d = 2;
    else if (e.key === 'Home') { setLeft(20, true); e.preventDefault(); return; }
    else if (e.key === 'End') { setLeft(70, true); e.preventDefault(); return; }
    if (d) { e.preventDefault(); setLeft(now + d, true); }
  });

  /* ---- status */
  function status(kind, text) {
    stDot.className = 'dot ' + (kind === 'ok' ? 'dot-running' : kind === 'busy' ? 'dot-waiting' : 'dot-idle');
    stTx.textContent = text;
    stDot.title = text.toLowerCase();
  }

  /* ---- navigation inside the signed prefix */
  function clean(raw) {
    var p = String(raw || '').trim();
    if (!p || /^[a-z][a-z0-9+.\-]*:/i.test(p) || p.indexOf('//') === 0 || p.indexOf('\\') >= 0) return null;
    p = p.replace(/^\/+/, '');
    var q = '', i = p.search(/[?#]/);
    if (i >= 0) { q = p.slice(i); p = p.slice(0, i); }
    var segs = p.split('/');
    for (var k = 0; k < segs.length; k++) {
      var s = segs[k];
      try { s = decodeURIComponent(s); } catch (e) { return null; }
      if (s === '..' || s === '.') return null;
    }
    return { path: p, q: q };
  }
  /* an absolute https URL whose origin is on the allowlist -> its href, else null */
  function allowed(raw) {
    var u;
    try { u = new URL(String(raw || '').trim()); } catch (e) { return null; }
    if (u.protocol !== 'https:' || u.username || u.password || u.origin === location.origin || hosts.indexOf(u.origin) < 0) return null;
    return u.href;
  }
  function urlFor(c) {
    return base + c.path.split('/').map(function (s) { return encodeURIComponent(decodeURIComponent(s)); }).join('/') + c.q;
  }
  /* Local pages share pm's origin: strict sandbox (opaque origin). An allowed external origin differs from pm's,
   * so it may keep its own origin: external <use href="x.svg#id"> sprites and fonts need it. */
  var SB = 'allow-scripts allow-forms allow-popups-to-escape-sandbox';
  function ensureFrame(isExt) {  // the empty state renders no iframe; make one when a page appears or the mode flips
    var sb = isExt ? SB + ' allow-same-origin' : SB;
    if (frame && frame.getAttribute('sandbox') === sb) return;
    var old = frame;  // sandbox flags apply on navigation: a new element is the reliable way to change them
    frame = document.createElement('iframe');
    frame.className = 'bld-frame';
    frame.id = 'builder-frame';
    frame.setAttribute('sandbox', sb);
    frame.addEventListener('load', onLoad);
    if (old) old.replaceWith(frame); else stage.insertBefore(frame, stage.firstChild);
  }
  /* source: "files" = a page of the project folder (path field, Files list), "url" = an allowed https page
   * (address field with the allowed origins as suggestions). The mode follows what loads. */
  function setMode(m) {
    srcSel.value = m;
    $('bld-files').hidden = m === 'url';
    pathIn.disabled = m === 'files' && !base;
    if (m === 'url') { pathIn.setAttribute('list', 'bld-hosts'); pathIn.placeholder = 'https://...'; }
    else { pathIn.removeAttribute('list'); pathIn.placeholder = '/index.html'; }
  }
  function showExt(href) {
    ext = lastExt = href;
    setMode('url');
    store(UK, href);
    ensureFrame(true);
    frame.hidden = false;
    if (empty) empty.hidden = true;
    status('busy', 'Loading');
    frame.title = 'Preview of ' + href;
    pathIn.value = href;
    pathIn.removeAttribute('aria-invalid');
    openA.href = href;
    frame.src = href;
    markCurrent();
  }
  function show(c) {
    setMode('files');
    if (ext) store(UK, null);
    ext = null;
    cur = c.path + c.q;
    var u = urlFor(c);
    ensureFrame(false);
    frame.hidden = false;
    if (empty) empty.hidden = true;
    status('busy', 'Loading');
    frame.title = 'Preview of /' + cur;
    pathIn.value = '/' + cur;
    openA.href = u;
    frame.src = u;
    markCurrent();
  }
  function go(raw) {
    var href = allowed(raw);
    if (href) { showExt(href); return true; }
    if (/^\s*([a-z][a-z0-9+.\-]*:|\/\/)/i.test(String(raw || ''))) {
      status('idle', 'URL not allowed');
      pathIn.setAttribute('aria-invalid', 'true');
      return false;
    }
    if (!base) return false;
    var c = clean(raw);
    if (!c) { status('idle', 'Invalid path'); pathIn.setAttribute('aria-invalid', 'true'); return false; }
    pathIn.removeAttribute('aria-invalid');
    show(c);
    return true;
  }
  function onLoad() { if (frame && !frame.hidden) status('ok', 'Loaded'); }
  if (frame) frame.addEventListener('load', onLoad);
  $('bld-pathform').addEventListener('submit', function (e) {
    e.preventDefault();
    var v = pathIn.value.trim();  // URL mode: a bare host means https
    if (srcSel.value === 'url' && v && v.charAt(0) !== '/' && !/^[a-z][a-z0-9+.\-]*:/i.test(v)) v = 'https://' + v;
    go(v);
  });
  srcSel.addEventListener('change', function () {
    var m = srcSel.value;
    if (m === 'url') {
      if (lastExt && allowed(lastExt)) { showExt(lastExt); return; }
      setMode('url'); pathIn.value = ''; pathIn.removeAttribute('aria-invalid'); status('idle', 'Enter a URL'); pathIn.focus();
    } else if (cur) { go(cur); } else { setMode('files'); findEntry(); }
  });
  $('bld-reload').addEventListener('click', function () {
    if (ext && frame) { status('busy', 'Loading'); frame.src = ext; return; }
    if (!base) return;
    if (!frame || frame.hidden) { findEntry(); return; }
    status('busy', 'Loading');
    var u = urlFor(clean(cur) || { path: '', q: '' });
    frame.src = u;
  });

  /* ---- files */
  var list = $('bld-list'), cnt = $('bld-cnt'), filesUrl = root.dataset.filesUrl, files = [];
  function markCurrent() {
    Array.prototype.forEach.call(list.querySelectorAll('a'), function (a) {
      if (!ext && a.dataset.path === cur) a.setAttribute('aria-current', 'page'); else a.removeAttribute('aria-current');
    });
  }
  function render() {
    list.textContent = '';
    cnt.textContent = String(files.length);
    if (!files.length) {
      var e = document.createElement('li');
      e.className = 'empty';
      e.textContent = 'No previewable files';
      list.appendChild(e);
      return;
    }
    files.forEach(function (f) {
      var li = document.createElement('li'), a = document.createElement('a');
      a.textContent = f;
      a.title = f;
      a.dataset.path = f;
      a.href = base + f.split('/').map(encodeURIComponent).join('/');
      li.appendChild(a);
      list.appendChild(li);
    });
    markCurrent();
  }
  list.addEventListener('click', function (e) {
    var a = e.target.closest && e.target.closest('a');
    if (!a) return;
    e.preventDefault();
    go(a.dataset.path);
  });
  function loadFiles(then) {
    if (!filesUrl) return;
    window.pm.fetch(filesUrl, { credentials: 'same-origin', cache: 'no-store', headers: { Accept: 'application/json' } })
      .then(function (r) { return r.ok ? r.json() : null; })
      .then(function (j) { if (j && Array.isArray(j.files)) { files = j.files; render(); if (then) then(); } })
      .catch(function () { /* next tick */ });
  }
  function findEntry() {
    loadFiles(function () {
      for (var i = 0; i < ENTRIES.length; i++) if (files.indexOf(ENTRIES[i]) >= 0) { go(ENTRIES[i]); return; }
    });
  }

  /* ---- poll for changes */
  var mtimeUrl = root.dataset.mtimeUrl, last = null, busy = false;
  function poll() {
    if (document.hidden || busy || !mtimeUrl || !base) return;
    busy = true;
    window.pm.fetch(mtimeUrl, { credentials: 'same-origin', cache: 'no-store', headers: { Accept: 'application/json' } })
      .then(function (r) { return r.ok ? r.json() : null; })
      .then(function (j) {
        if (!j) return;
        var key = j.mtime + ':' + j.n;
        if (last !== null && key !== last) {
          root.dispatchEvent(new CustomEvent('bld:changed'));  // builder-api.js reads the spec again
          var none = !frame || frame.hidden;
          loadFiles(none ? findEntryFromFiles : null);
          if (!none && !ext) { status('busy', 'Loading'); frame.src = urlFor(clean(cur) || { path: '', q: '' }); }
        }
        last = key;
      })
      .catch(function () { /* offline */ })
      .then(function () { busy = false; });
  }
  function findEntryFromFiles() {
    for (var i = 0; i < ENTRIES.length; i++) if (files.indexOf(ENTRIES[i]) >= 0) { go(ENTRIES[i]); return; }
  }
  var lastUrl = load(UK);
  if (lastUrl && allowed(lastUrl)) showExt(allowed(lastUrl)); else if (!base) setMode('url');
  if (!base) srcSel.querySelector('option[value="files"]').disabled = true;  // no project folder: URL only
  if (base) {
    loadFiles();
    setInterval(poll, 2000);
    poll();
  }

  /* ---- chat: keep the newest message in view unless the reader scrolled up */
  var msgs = $('bld-msgs'), stick = true;
  msgs.addEventListener('scroll', function () { stick = msgs.scrollTop + msgs.clientHeight >= msgs.scrollHeight - 40; });
  function toBottom() { if (stick) msgs.scrollTop = msgs.scrollHeight; }
  msgs.addEventListener('nodiff:swapped', toBottom);
  document.body.addEventListener('htmx:afterSwap', function (e) { if (msgs.contains(e.target)) toBottom(); });

  /* ---- "Preview: <path or allowed URL>" on its own line in the newest assistant reply navigates the aside,
   * once per reply (data-seq). The replies on show when the chat first loads are the baseline: reopening the
   * builder does not jump; only a reply that appears or changes after that counts. */
  var PREVIEW_RE = /^Preview:\s*(\S+)\s*$/i;
  var ci = $('ci'), baseSeq = null, baseTarget = null, done = {};
  function lines(n) {  // text by line: <br> and block ends become newlines (textContent alone glues <p>s)
    var c = n.cloneNode(true);
    Array.prototype.forEach.call(c.querySelectorAll('br'), function (b) { b.replaceWith('\n'); });
    Array.prototype.forEach.call(c.querySelectorAll('p, li, div, pre, blockquote, h1, h2, h3, h4, h5, h6, tr'),
      function (b) { b.append('\n'); });
    return c.textContent.split('\n');
  }
  function previewOf(n) {
    var body = n.querySelector('.chat-text') || n, ls = lines(body);
    for (var i = ls.length - 1; i >= 0; i--) {
      var m = PREVIEW_RE.exec(ls[i].trim());
      if (m) return m[1].replace(/^[`'"<]+|[`'">]+$/g, '');
    }
    return null;
  }
  function onReplies() {
    if (!ci.querySelector('.ci-items')) return;  // still "Loading..."
    var all = ci.querySelectorAll('.ci-text[data-seq]'), n = all.length ? all[all.length - 1] : null;
    var seq = n ? n.getAttribute('data-seq') : '', target = n ? previewOf(n) : null;
    if (baseSeq === null) { baseSeq = seq; baseTarget = target; return; }
    if (!target || done[seq] || (seq === baseSeq && target === baseTarget)) return;
    done[seq] = true;
    go(target);
  }
  if (ci) ci.addEventListener('pm:ci-update', onReplies);

  /* ---- "Preview: <target>" in a reply becomes a link, and a plain click on it (or on a chat link to an allowed
   * URL) opens it in the aside; ctrl/cmd/shift/middle click keeps the normal new tab. */
  var PLINK = /^(\s*Preview:\s*)([^\s`'"<>]+)\s*$/i;  // the line is only "Preview: <target>"
  function previewTarget(raw) { return allowed(raw) ? raw : (clean(raw) && base ? raw : null); }
  function linkify(textRoot) {
    var w = document.createTreeWalker(textRoot, NodeFilter.SHOW_TEXT, {
      acceptNode: function (t) {
        if (!PLINK.test(t.nodeValue)) return NodeFilter.FILTER_REJECT;
        return t.parentNode.closest('a, button') ? NodeFilter.FILTER_REJECT : NodeFilter.FILTER_ACCEPT;
      }
    }), nodes = [], n;
    while ((n = w.nextNode())) nodes.push(n);
    nodes.forEach(function (t) {
      var m = PLINK.exec(t.nodeValue), target = m && previewTarget(m[2]);
      if (!target) return;
      var a = document.createElement('a'), at = m.index + m[1].length;
      a.className = 'bld-plink';
      a.dataset.preview = target;
      a.href = allowed(target) || urlFor(clean(target));
      a.title = 'Open in the preview';
      a.textContent = m[2];
      var rest = t.splitText(at);
      rest.nodeValue = rest.nodeValue.slice(m[2].length);
      t.parentNode.insertBefore(a, rest);
    });
  }
  function linkPreviews() { Array.prototype.forEach.call(msgs.querySelectorAll('.chat-text'), linkify); }
  document.addEventListener('pm:ci-update', linkPreviews);
  document.addEventListener('htmx:afterSettle', linkPreviews);
  msgs.addEventListener('click', function (e) {
    if (e.defaultPrevented || e.button !== 0 || e.ctrlKey || e.metaKey || e.shiftKey || e.altKey) return;
    var a = e.target.closest && e.target.closest('a[href]');
    if (!a || !msgs.contains(a)) return;
    var target = a.dataset.preview || (allowed(a.href) ? a.href : null);
    if (!target) return;
    e.preventDefault();
    $('bt-prev').checked = true;  // phones: switch to the Preview tab (no effect on the split layout)
    go(target);
  });

})();
