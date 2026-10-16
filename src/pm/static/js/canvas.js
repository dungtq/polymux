/* Canvas: open an assistant reply as editable markdown in the side pane (#mdpane, shared with the file viewer).
   Server contract: /a/{id}/canvas (list, create), /canvas/{cid} (get), .../save|restore|delete, /canvas/preview.
   The Canvas button lives inside #chat (re-rendered every 2s), so clicks are delegated. */
(function () {
  'use strict';
  var d = document, split = d.getElementById('chatsplit'), pane = d.getElementById('mdpane');
  if (!split || !pane) return;

  // ---- chat | pane divider (desktop only; CSS hides it otherwise). Ratio = chat share in %, kept in localStorage. ----
  (function () {
    var SK = 'pm.chatsplit', DEF = 50, MINL = 320, MINR = 360, DW = 12, pct = DEF;
    var dv = d.createElement('div');
    dv.className = 'cs-div'; dv.setAttribute('role', 'separator'); dv.setAttribute('aria-orientation', 'vertical');
    dv.setAttribute('aria-label', 'Resize chat and side pane'); dv.tabIndex = 0;
    dv.title = 'Drag to resize, double-click to reset';
    split.insertBefore(dv, pane);
    try { var v = parseFloat(localStorage.getItem(SK)); if (v > 0 && v < 100) pct = v; } catch (e) { /* ignore */ }
    function bounds() {
      var w = split.getBoundingClientRect().width - DW;
      if (w <= MINL + MINR) return { lo: DEF, hi: DEF };
      return { lo: Math.ceil(MINL / w * 100), hi: Math.floor((1 - MINR / w) * 100) };
    }
    function paint() {
      var b = bounds(), p = Math.min(b.hi, Math.max(b.lo, pct));
      split.style.setProperty('--cs-l', p + 'fr'); split.style.setProperty('--cs-r', (100 - p) + 'fr');
      dv.setAttribute('aria-valuemin', b.lo); dv.setAttribute('aria-valuemax', b.hi); dv.setAttribute('aria-valuenow', Math.round(p));
      dv.setAttribute('aria-valuetext', Math.round(p) + '% chat, ' + (100 - Math.round(p)) + '% side pane');
      return p;
    }
    function save(clear) { try { if (clear) localStorage.removeItem(SK); else localStorage.setItem(SK, String(pct)); } catch (e) { /* ignore */ } }
    function setPct(p, persist) { pct = p; pct = paint(); if (persist) save(); }
    dv.addEventListener('pointerdown', function (e) {
      if (e.button) return;
      e.preventDefault(); dv.setPointerCapture(e.pointerId); dv.classList.add('is-drag'); split.classList.add('is-dragging');
    });
    dv.addEventListener('pointermove', function (e) {
      if (!dv.classList.contains('is-drag')) return;
      var r = split.getBoundingClientRect(), w = r.width - DW;
      if (w > 0) setPct((e.clientX - r.left - DW / 2) / w * 100, false);
    });
    function end() { if (!dv.classList.contains('is-drag')) return; dv.classList.remove('is-drag'); split.classList.remove('is-dragging'); save(); }
    dv.addEventListener('pointerup', end); dv.addEventListener('pointercancel', end); dv.addEventListener('lostpointercapture', end);
    dv.addEventListener('dblclick', function () { pct = DEF; paint(); save(true); });
    dv.addEventListener('keydown', function (e) {
      var step = e.shiftKey ? 10 : 2, b = bounds(), cur = paint();
      if (e.key === 'ArrowLeft') setPct(cur - step, true);
      else if (e.key === 'ArrowRight') setPct(cur + step, true);
      else if (e.key === 'Home') setPct(b.lo, true);
      else if (e.key === 'End') setPct(b.hi, true);
      else if (e.key === 'Enter') { pct = DEF; paint(); save(true); }
      else return;
      e.preventDefault();
    });
    paint();
    if (window.ResizeObserver) new ResizeObserver(function () { paint(); }).observe(split);
    else addEventListener('resize', paint);
  })();
  var cv = pane.querySelector('.cv');
  if (!cv) return;
  var aid = split.getAttribute('data-agent');
  var base = '/a/' + encodeURIComponent(aid) + '/canvas';
  function q(s) { return cv.querySelector(s); }
  // the editor is created here (a textarea in the template would precede the composer's in the DOM)
  var edw = q('.cv-edw'), ed = d.createElement('textarea');
  ed.className = 'cv-ed'; ed.spellcheck = false; ed.setAttribute('aria-label', 'Canvas markdown');
  edw.replaceWith(ed);
  var pv = q('.cv-pv'), state = q('.cv-state'), hist = q('.cv-hist'), bd = q('.cv-body'),
    note = q('.cv-note'), noteT = q('.cv-note-t'), noteB = q('[data-cv-reload]'), title = q('.cv-t'),
    pick = q('.cv-pick'), ask = q('[data-cv-ask]'), saveB = q('[data-cv-save]');
  var st = { id: null, cur: 0, versions: [], saved: '', stale: false, busy: false, mode: 'edit' };
  var KEY = 'pm.canvas.' + aid;

  function composer() { return d.querySelector('[data-composer][data-agent] [data-cmp-input]'); }
  function active() { return pane.classList.contains('is-canvas') && !pane.hidden; }
  function dirty() { return st.id !== null && ed.value !== st.saved; }

  function api(method, path, fields) {
    var o = { method: method, credentials: 'same-origin', headers: { Accept: 'application/json' } };
    if (method === 'POST') {
      var p = new URLSearchParams();
      Object.keys(fields || {}).forEach(function (k) { p.set(k, fields[k]); });
      o.body = p; o.csrf = 'both';
      o.headers['Content-Type'] = 'application/x-www-form-urlencoded';
    }
    return window.pm.fetch(base + path, o).then(function (r) {
      return r.json().catch(function () { return {}; }).then(function (j) { return { ok: r.ok, status: r.status, json: j }; });
    });
  }

  function say(msg, reload) { noteT.textContent = msg || ''; noteB.hidden = !reload; note.hidden = !msg; }
  function fail(r, what) { say(what + ' failed' + (r && r.status ? ' (' + r.status + ')' : '') + (r && r.json && r.json.message ? ': ' + r.json.message : '.')); }

  // ---- view state ----
  function paintState() {
    var label = st.busy ? 'saving…' : st.stale ? 'out of date' : dirty() ? 'unsaved edits' : 'saved';
    state.textContent = label;
    state.classList.toggle('dirty', dirty() || st.stale);
    saveB.disabled = st.busy || !dirty();
  }
  var pvT = 0, pvSeq = 0;
  function preview() {
    clearTimeout(pvT);
    pvT = setTimeout(function () {
      if (st.mode === 'edit' || st.id === null) return;
      var n = ++pvSeq;
      api('POST', '/preview', { text: ed.value }).then(function (r) {
        if (n !== pvSeq) return;
        if (r.ok && typeof r.json.html === 'string') pv.innerHTML = r.json.html; // server-rendered, escaped markdown
        else pv.textContent = 'Preview unavailable.';
      }).catch(function () { if (n === pvSeq) pv.textContent = 'Preview unavailable.'; });
    }, 250);
  }
  function setMode(m) {
    st.mode = m; bd.setAttribute('data-mode', m);
    [].forEach.call(cv.querySelectorAll('[data-cv-m]'), function (b) { b.setAttribute('aria-pressed', String(b.getAttribute('data-cv-m') === m)); });
    if (m !== 'edit') preview();
  }
  function when(ts) {
    var t = typeof ts === 'number' ? new Date(ts < 1e12 ? ts * 1000 : ts) : new Date(ts);
    if (isNaN(t)) return String(ts || '');
    var p = function (n) { return (n < 10 ? '0' : '') + n; };
    return p(t.getHours()) + ':' + p(t.getMinutes());
  }
  function paintHist() {
    hist.textContent = '';
    st.versions.slice().reverse().forEach(function (v) {
      var li = d.createElement('li'), a = d.createElement('span'), b = d.createElement('span');
      a.className = 'mono'; a.textContent = 'v' + v.n;
      b.className = 'muted cv-vm'; b.textContent = [when(v.ts), v.note].filter(Boolean).join(' · ');
      li.appendChild(a); li.appendChild(b);
      if (v.n === st.cur) { var c = d.createElement('span'); c.className = 'muted'; c.textContent = '· current'; li.appendChild(c); }
      else { var r = d.createElement('button'); r.type = 'button'; r.className = 'small'; r.textContent = 'Restore'; r.setAttribute('data-cv-restore', v.n); li.appendChild(r); }
      hist.appendChild(li);
    });
  }
  function curText(c) {
    var v = (c.versions || []).filter(function (x) { return x.n === c.cur; })[0];
    return v ? v.text : '';
  }
  // Replace editor + versions from a server canvas object.
  function apply(c, keepEditor) {
    st.id = c.id; st.cur = c.cur; st.versions = c.versions || []; st.stale = false;
    var t = curText(c);
    if (!keepEditor) ed.value = t;
    st.saved = t;
    title.textContent = 'Canvas · ' + (c.title || 'Reply');
    title.title = title.textContent;
    paintHist(); paintState(); preview();
  }
  function refresh(keepEditor) {
    return api('GET', '/' + encodeURIComponent(st.id)).then(function (r) {
      if (!r.ok) { fail(r, 'Loading the canvas'); return false; }
      apply(r.json, keepEditor); return true;
    });
  }
  function loadPick(sel) {
    api('GET', '').then(function (r) {
      var list = (r.ok && r.json.canvases) || [];
      pick.textContent = '';
      list.forEach(function (c) {
        var o = d.createElement('option'); o.value = c.id; o.textContent = (c.title || 'Reply') + ' · v' + c.cur;
        pick.appendChild(o);
      });
      pick.value = sel || st.id; pick.hidden = list.length < 2;
    });
  }

  // ---- show / hide ----
  function show() {
    pane.classList.add('is-canvas'); pane.hidden = false; split.classList.add('has-md'); cv.hidden = false;
    if (!window.matchMedia || !window.matchMedia('(min-width: 900px)').matches) pane.scrollIntoView({ block: 'nearest' });
  }
  function leave() { // back to the plain file viewer; local canvas state is dropped
    pane.classList.remove('is-canvas'); cv.hidden = true; st.id = null; st.stale = false; say('');
    selBtn.classList.remove('on');
  }
  // in-page modal (dialog.js), never the native popup: always a Promise<boolean>
  function askYN(title, label) { return window.pmConfirm({ title: title, confirmLabel: label || 'Discard', danger: true }).then(function (ok) { return !!ok; }); }
  function confirmDiscard() { return dirty() ? askYN('Discard unsaved canvas edits?') : Promise.resolve(true); }

  // ---- map reply -> canvas id (so reopening the same reply reuses its canvas) ----
  function hash(s) { var h = 5381, i; for (i = 0; i < s.length; i++) h = ((h << 5) + h + s.charCodeAt(i)) | 0; return String(h) + ':' + s.length; }
  function mapGet(k) { try { return (JSON.parse(localStorage.getItem(KEY) || '{}'))[k] || null; } catch (e) { return null; } }
  function mapSet(k, v) { try { var m = JSON.parse(localStorage.getItem(KEY) || '{}'); if (v) m[k] = v; else delete m[k]; localStorage.setItem(KEY, JSON.stringify(m)); } catch (e) { /* ignore */ } }
  function titleOf(raw) {
    var l = raw.split('\n').filter(function (x) { return x.trim(); })[0] || 'Reply';
    l = l.replace(/^[#>\-*\s]+/, '').replace(/[`*_]/g, '').trim();
    return (l || 'Reply').slice(0, 60);
  }

  function openReply(raw) {
    if (active()) { confirmDiscard().then(function (ok) { if (ok) openReplyGo(raw); }); return; }
    openReplyGo(raw);
  }
  function openReplyGo(raw) {
    say('');
    var k = hash(raw), known = mapGet(k);
    show(); title.textContent = 'Canvas · opening…';
    function create() {
      return api('POST', '', { title: titleOf(raw), text: raw }).then(function (r) {
        if (!r.ok || !r.json.id) { fail(r, 'Creating the canvas'); return; }
        mapSet(k, r.json.id); st.id = r.json.id;
        return refresh(false).then(function (ok) { if (ok) done(); });
      });
    }
    function done() { setMode(window.innerWidth >= 1400 ? 'split' : 'edit'); loadPick(); ed.focus(); }
    if (known) {
      st.id = known;
      refresh(false).then(function (ok) { if (ok) done(); else { mapSet(k, null); st.id = null; return create(); } });
    } else create();
  }
  function openId(id) {
    st.id = id;
    refresh(false).then(function (ok) { if (ok) { setMode(st.mode); loadPick(id); } });
  }

  // ---- actions ----
  function save() {
    if (!dirty() || st.busy) return;
    var text = ed.value; st.busy = true; paintState();
    api('POST', '/' + encodeURIComponent(st.id) + '/save', { text: text, base: st.cur }).then(function (r) {
      st.busy = false;
      if (r.status === 409) { st.stale = true; paintState(); say('Out of date: this canvas changed elsewhere' + (r.json && r.json.cur ? ' (now v' + r.json.cur + ')' : '') + '. Your edits are kept here.', true); return; }
      if (!r.ok) { paintState(); fail(r, 'Save'); return; }
      say('');
      refresh(true).then(function () { st.saved = text; paintState(); });
    }).catch(function () { st.busy = false; paintState(); say('Save failed (network).'); });
  }
  function restore(n) {
    if (dirty()) { askYN('Replace your unsaved edits with v' + n + '?', 'Replace').then(function (ok) { if (ok) restoreGo(n); }); return; }
    restoreGo(n);
  }
  function restoreGo(n) {
    api('POST', '/' + encodeURIComponent(st.id) + '/restore', { n: n }).then(function (r) {
      if (!r.ok) { fail(r, 'Restore'); return; }
      say(''); refresh(false);
    });
  }
  function del() {
    askYN('Delete this canvas and all its versions?', 'Delete').then(function (ok) { if (ok) delGo(); });
  }
  function delGo() {
    var id = st.id;
    api('POST', '/' + encodeURIComponent(id) + '/delete', {}).then(function (r) {
      if (!r.ok) { fail(r, 'Delete'); return; }
      try { var m = JSON.parse(localStorage.getItem(KEY) || '{}'); Object.keys(m).forEach(function (k) { if (m[k] === id) delete m[k]; }); localStorage.setItem(KEY, JSON.stringify(m)); } catch (e) { /* ignore */ }
      st.saved = ed.value; // nothing left to protect
      leave(); var c = pane.querySelector('[data-md-close]'); if (c) c.click();
    });
  }
  function toComposer(text) {
    var ta = composer();
    if (!ta) { say('No message box on this page.'); return; }
    ta.value = text; ta.dispatchEvent(new Event('input', { bubbles: true })); ta.focus();
    var max = parseInt(ta.getAttribute('maxlength') || '0', 10);
    say(max && text.length > max ? 'Document is ' + text.length + ' characters; the message box limit is ' + max + '. Trim it before sending.' : '');
  }

  // ---- events ----
  d.addEventListener('click', function (e) {
    var b = e.target.closest && e.target.closest('[data-canvas-open]');
    if (b) { e.preventDefault(); openReply(b.getAttribute('data-raw') || ''); }
  });
  // A markdown file link while the canvas is open switches the pane back to the file viewer.
  d.addEventListener('click', function (e) {
    if (!active() || !e.target.closest) return;
    var close = e.target.closest('[data-md-close]');
    var link = e.target.closest('a[data-md]');
    if (!(close && cv.contains(close)) && !link) return;
    var btn = close && cv.contains(close) ? close : link;
    if (btn.__cvOk) { btn.__cvOk = false; leave(); return; }  // confirmed: let the click through
    if (!dirty()) { leave(); return; }
    e.stopPropagation(); e.preventDefault();
    confirmDiscard().then(function (ok) { if (ok) { btn.__cvOk = true; btn.click(); } });
  }, true);
  cv.addEventListener('click', function (e) {
    var t = e.target.closest('button'); if (!t) return;
    if (t.hasAttribute('data-cv-m')) setMode(t.getAttribute('data-cv-m'));
    else if (t.hasAttribute('data-cv-restore')) restore(+t.getAttribute('data-cv-restore'));
    else if (t.hasAttribute('data-cv-save')) save();
    else if (t.hasAttribute('data-cv-del')) del();
    else if (t.hasAttribute('data-cv-reload')) {
      var reload = function () { say(''); refresh(false); };
      if (dirty()) askYN('Discard your edits and load the latest version?').then(function (ok) { if (ok) reload(); }); else reload();
    }
    else if (t.hasAttribute('data-cv-send')) toComposer('Use this edited document as the new version:\n\n' + ed.value);
    else if (t.hasAttribute('data-cv-copy')) {
      var done = function (ok) { t.textContent = ok ? 'Copied' : 'Copy failed'; setTimeout(function () { t.textContent = 'Copy'; }, 1200); };
      window.pm.copy(ed.value).then(done);
    }
  });
  ed.addEventListener('input', function () { paintState(); preview(); });
  pick.addEventListener('change', function () {
    if (pick.value === st.id) return;
    var want = pick.value;
    confirmDiscard().then(function (ok) { if (ok) openId(want); else pick.value = st.id; });
  });
  function askGo() {
    var i = ask.querySelector('input'), v = i.value.trim();
    if (!v) return;
    toComposer('Revise this document: ' + v + '\n\n---\n' + ed.value); i.value = '';
  }
  ask.querySelector('[data-cv-askgo]').addEventListener('click', askGo);
  ask.querySelector('input').addEventListener('keydown', function (e) { if (e.key === 'Enter') { e.preventDefault(); askGo(); } });
  d.addEventListener('keydown', function (e) {
    if (active() && (e.ctrlKey || e.metaKey) && !e.altKey && (e.key === 's' || e.key === 'S')) { e.preventDefault(); save(); }
  });
  addEventListener('beforeunload', function (e) { if (active() && dirty()) { e.preventDefault(); e.returnValue = ''; } });

  // full-screen label: chat.js toggles .is-full; mirror it on our button
  var fb = q('[data-md-full]');
  new MutationObserver(function () {
    var on = pane.classList.contains('is-full');
    fb.setAttribute('aria-pressed', String(on)); fb.textContent = on ? 'Exit full screen' : 'Full screen'; fb.setAttribute('aria-label', fb.textContent);
  }).observe(pane, { attributes: true, attributeFilter: ['class'] });

  // selection -> ask the agent to edit just that part
  var selBtn = d.createElement('button');
  selBtn.type = 'button'; selBtn.className = 'small cv-sel'; selBtn.textContent = 'Ask agent to edit selection';
  d.body.appendChild(selBtn);
  function onSel() {
    var s = ed.value.slice(ed.selectionStart, ed.selectionEnd);
    if (!s.trim()) { selBtn.classList.remove('on'); return; }
    var r = ed.getBoundingClientRect();
    selBtn.style.left = Math.max(r.left, r.right - 200) + 'px'; selBtn.style.top = Math.max(8, r.top - 34) + 'px';
    selBtn.classList.add('on');
  }
  ed.addEventListener('mouseup', onSel); ed.addEventListener('keyup', onSel);
  ed.addEventListener('blur', function () { setTimeout(function () { selBtn.classList.remove('on'); }, 200); });
  selBtn.addEventListener('click', function () {
    var s = ed.value.slice(ed.selectionStart, ed.selectionEnd);
    toComposer('Edit only this part of the document:\n\n> ' + s.replace(/\n/g, '\n> ') + '\n\nChange: ');
    selBtn.classList.remove('on');
  });
})();
