/* Builder preview aside: Page | API switch and the API pane (endpoints from openapi.json / mocks, a test request,
 * the response). Mock mode (default) answers in the browser from the examples GET <api-url> returned; a listed
 * dev-server port (setting builder_api_ports) sends through POST <api-url>/send (CSRF header, pm.fetch).
 * Responses are shown as text only (textContent), never as HTML or in a frame. Loaded after builder.js. */
(function () {
  'use strict';
  var root = document.getElementById('builder');
  if (!root || !root.dataset.apiUrl) return;
  var $ = function (id) { return document.getElementById(id); };
  var apiUrl = root.dataset.apiUrl, pane = $('bld-api'), seg = $('bld-view');
  var note = $('bld-apinote'), list = $('bld-eps'), work = $('bld-work'), target = $('bld-target');
  var mSel = $('bld-m'), pathIn = $('bld-rpath'), qIn = $('bld-q'), hIn = $('bld-h'), bIn = $('bld-b'), rsSel = $('bld-rs');
  var res = $('bld-res'), resDot = $('bld-resdot'), resTx = $('bld-restx'), resB = $('bld-resb');
  var resH = $('bld-resha'), resHn = $('bld-reshn'), srcTx = $('bld-apisrc'), sendBtn = $('bld-send');
  var bs = (window.pm && window.pm.builder) || { load: function () { return null; }, store: function () {} };
  var VK = 'pm-builder-view:' + (root.dataset.agent || '');
  var eps = [], cur = -1, loaded = false, stale = false, busy = false;

  /* ---- Page | API */
  function setView(v, save) {
    root.dataset.view = v;
    pane.hidden = v !== 'api';
    Array.prototype.forEach.call(seg.querySelectorAll('button'), function (b) {
      b.setAttribute('aria-pressed', String(b.dataset.bldView === v));
    });
    if (save) bs.store(VK, v === 'api' ? 'api' : null);
    if (v === 'api' && (!loaded || stale)) fetchApi();
  }
  seg.addEventListener('click', function (e) {
    var b = e.target.closest && e.target.closest('button[data-bld-view]');
    if (b) setView(b.dataset.bldView, true);
  });
  root.addEventListener('bld:changed', function () { if (root.dataset.view === 'api') fetchApi(); else stale = true; });
  $('bld-api-reload').addEventListener('click', function () { fetchApi(); });

  /* ---- endpoint list */
  function fetchApi() {
    if (busy) return;
    busy = true;
    window.pm.fetch(apiUrl, { cache: 'no-store', headers: { Accept: 'application/json' } })
      .then(function (r) { return r.ok ? r.json() : null; })
      .then(function (j) { if (j) apply(j); })
      .catch(function () { /* offline: keep what is shown */ })
      .then(function () { busy = false; });
  }
  function apply(j) {
    loaded = true; stale = false;
    var keep = cur >= 0 && eps[cur] ? eps[cur].method + ' ' + eps[cur].path : null;
    eps = Array.isArray(j.endpoints) ? j.endpoints : [];
    ports(j.ports || []);
    var n = eps.length;
    srcTx.textContent = j.source ? j.source + ' · ' + n + (n === 1 ? ' endpoint' : ' endpoints') + (j.truncated ? ' (first ' + n + ')' : '') : '';
    srcTx.title = j.title || '';
    note.hidden = n > 0;
    list.hidden = work.hidden = n === 0;
    note.querySelector('h2').textContent = j.error ? 'API file refused' : 'No API found';
    note.querySelector('p').hidden = !!j.error;
    var err = note.querySelector('.err') || note.appendChild(document.createElement('p'));
    err.className = 'err';
    err.textContent = j.error || '';
    list.textContent = '';
    eps.forEach(function (e, i) {
      var li = document.createElement('li'), b = document.createElement('button'), m = document.createElement('span');
      var p = document.createElement('span'), s = document.createElement('span');
      b.type = 'button'; b.dataset.i = String(i); b.title = e.method + ' ' + e.path + (e.summary ? ' - ' + e.summary : '');
      m.className = 'm'; m.textContent = e.method;
      p.className = 'p'; p.textContent = e.path;
      s.className = 's'; s.textContent = e.summary || '';
      b.append(m, p, s); li.appendChild(b); list.appendChild(li);
    });
    var again = -1;
    eps.forEach(function (e, i) { if (again < 0 && e.method + ' ' + e.path === keep) again = i; });
    if (again >= 0) mark(again); else if (n) pick(0); else cur = -1;
  }
  function ports(ps) {
    var was = target.value;
    Array.prototype.slice.call(target.options, 1).forEach(function (o) { o.remove(); });
    ps.forEach(function (p) { var o = document.createElement('option'); o.value = String(p); o.textContent = '127.0.0.1:' + p; target.appendChild(o); });
    target.disabled = !ps.length;
    target.value = ps.indexOf(Number(was)) >= 0 ? was : 'mock';
    mockMode();
  }
  function mark(i) {
    cur = i;
    Array.prototype.forEach.call(list.querySelectorAll('button'), function (b) {
      if (Number(b.dataset.i) === i) b.setAttribute('aria-current', 'true'); else b.removeAttribute('aria-current');
    });
  }
  function text(v) { return v === null || v === undefined ? '' : typeof v === 'string' ? v : JSON.stringify(v, null, 2); }
  function pick(i) {
    var e = eps[i];
    if (!e) return;
    mark(i);
    var path = e.path, q = [], h = [];
    (e.params || []).forEach(function (p) {
      var ex = p.example === null || p.example === undefined ? '' : String(typeof p.example === 'object' ? JSON.stringify(p.example) : p.example);
      if (p.in === 'path' && ex) path = path.split('{' + p.name + '}').join(encodeURIComponent(ex));
      else if (p.in === 'query' && (p.required || ex)) q.push(encodeURIComponent(p.name) + '=' + encodeURIComponent(ex));
      else if (p.in === 'header' && p.required) h.push(p.name + ': ' + ex);
    });
    mSel.value = e.method;
    pathIn.value = path; qIn.value = q.join('&'); hIn.value = h.join('\n');
    bIn.value = text(e.request);
    if (e.request !== null && e.request !== undefined && typeof e.request !== 'string' && !/^content-type:/im.test(hIn.value)) {
      hIn.value = (hIn.value ? hIn.value + '\n' : '') + 'Content-Type: application/json';
    }
    rsSel.textContent = '';
    (e.responses || []).forEach(function (r, k) {
      var o = document.createElement('option');
      o.value = String(k); o.textContent = r.status + (r.desc ? ' ' + r.desc : '');
      rsSel.appendChild(o);
    });
    var ok = (e.responses || []).findIndex(function (r) { return /^2/.test(r.status); });
    rsSel.value = String(ok >= 0 ? ok : 0);
    rsSel.disabled = !(e.responses || []).length;
  }
  list.addEventListener('click', function (ev) {
    var b = ev.target.closest && ev.target.closest('button[data-i]');
    if (b) pick(Number(b.dataset.i));
  });

  /* ---- send */
  function mockMode() { root.classList.toggle('bld-real', target.value !== 'mock'); }
  target.addEventListener('change', mockMode);
  function split() {  // "/x?a=1" typed into the path: the query goes to the Query field
    var p = pathIn.value.trim(), i = p.indexOf('?');
    if (i >= 0) { qIn.value = (qIn.value ? qIn.value + '&' : '') + p.slice(i + 1); p = p.slice(0, i); pathIn.value = p; }
    return p;
  }
  function headers() {
    var o = {}, bad = null;
    hIn.value.split('\n').forEach(function (ln) {
      if (!ln.trim()) return;
      var i = ln.indexOf(':');
      if (i < 1) { bad = ln; return; }
      o[ln.slice(0, i).trim()] = ln.slice(i + 1).trim();
    });
    return bad === null ? o : null;
  }
  function tmplMatch(tpl, path) {
    var re = '^' + tpl.split(/\{[^}/]+\}/).map(function (s) { return s.replace(/[.*+?^$()|[\]\\]/g, '\\$&'); }).join('[^/]+') + '/?$';
    return new RegExp(re).test(path);
  }
  function mockReply(method, path) {
    var e = eps[cur], hit = null;
    if (e && e.method === method && tmplMatch(e.path, path)) hit = e;
    eps.forEach(function (x) { if (!hit && x.method === method && tmplMatch(x.path, path)) hit = x; });
    if (!hit) return { status: 404, reason: 'No mock', headers: [], body: 'No endpoint in the spec or mocks matches ' + method + ' ' + path + '.' };
    var rs = hit.responses || [], r = hit === e ? rs[Number(rsSel.value)] : null;
    if (!r) r = rs.filter(function (x) { return /^2/.test(x.status); })[0] || rs[0];
    if (!r) return { status: 204, reason: 'No example', headers: [], body: '' };
    var hs = Object.keys(r.headers || {}).map(function (k) { return [k, String(r.headers[k])]; });
    return { status: r.status, reason: r.desc || '', headers: hs, body: method === 'HEAD' ? '' : text(r.body) };
  }
  function show(r, how) {
    res.hidden = false;
    var code = parseInt(r.status, 10);
    resDot.className = 'dot ' + (code >= 200 && code < 400 ? 'dot-running' : code >= 400 || !code ? 'dot-failed' : 'dot-waiting');
    resDot.title = String(r.status);
    resTx.textContent = r.status + (r.reason ? ' ' + r.reason : '') + ' · ' + how + (r.ms !== undefined ? ' · ' + r.ms + ' ms' : '') + (r.truncated ? ' · cut at 512 KB' : '');
    var body = String(r.body || '');
    try { if (/^\s*[[{]/.test(body)) body = JSON.stringify(JSON.parse(body), null, 2); } catch (x) { /* not JSON: as is */ }
    resB.textContent = body || '(empty body)';
    resH.textContent = (r.headers || []).map(function (h) { return h[0] + ': ' + h[1]; }).join('\n');
    resHn.textContent = String((r.headers || []).length);
  }
  function fail(msg) { show({ status: 'Error', reason: '', headers: [], body: msg }, target.value === 'mock' ? 'mock' : '127.0.0.1:' + target.value); }
  $('bld-req').addEventListener('submit', function (ev) {
    ev.preventDefault();
    var method = mSel.value, path = split(), hs = headers();
    if (!/^\/(?!\/)/.test(path)) { fail('The path must start with a single /.'); return; }
    if (hs === null) { fail('Headers: one "Name: value" per line.'); return; }
    if (target.value === 'mock') { show(mockReply(method, path), 'mock'); return; }
    sendBtn.disabled = true;
    window.pm.fetch(apiUrl + '/send', {
      method: 'POST', csrf: 'header', cache: 'no-store',
      headers: { 'Content-Type': 'application/json', Accept: 'application/json' },
      body: JSON.stringify({ port: Number(target.value), method: method, path: path, query: qIn.value.trim(), headers: hs, body: bIn.value })
    }).then(function (r) {
      return r.json().then(function (j) { if (!r.ok) throw new Error(j.error || j.detail || ('HTTP ' + r.status)); return j; },
        function () { throw new Error('HTTP ' + r.status); });
    }).then(function (j) { show(j, '127.0.0.1:' + target.value); })
      .catch(function (e) { fail(String(e.message || e)); })
      .then(function () { sendBtn.disabled = false; });
  });

  if (bs.load(VK) === 'api') setView('api', false);
})();
