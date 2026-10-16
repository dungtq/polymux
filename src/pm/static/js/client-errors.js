/* Report browser errors to POST /client-errors (path + status + short message + stack only; the server masks). */
(function () {
  'use strict';
  var ENDPOINT = '/client-errors', sent = 0, recent = {}, MAX = 25, leaving = false;
  window.addEventListener('pagehide', function () { leaving = true; });
  window.addEventListener('pageshow', function () { leaving = false; });  // back from the bfcache

  function csrf() { return window.pm ? window.pm.csrf() : ''; }
  function path(u) {
    try { return new URL(u, location.href).pathname; } catch (e) { return String(u || '').split(/[?#]/)[0]; }
  }
  function origin(u) {
    try { return new URL(u).origin; } catch (e) { return String(u || '').split(/[/?#]/).slice(0, 3).join('/'); }
  }
  function stack(x) { return x && x.stack ? String(x.stack).slice(0, 1500) : ''; }
  function report(e) {
    if (sent >= MAX || !csrf()) return;
    var key = [e.kind, e.url, e.status, e.msg].join('|'), now = Date.now();
    if (recent[key] && now - recent[key] < 5000) return;
    recent[key] = now;
    sent++;
    e.page = location.pathname;
    try {
      window.pm.fetch(ENDPOINT, {
        method: 'POST', credentials: 'same-origin', keepalive: true, csrf: 'header',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(e)
      }).catch(function () {});
    } catch (x) { /* never throw from the reporter */ }
  }

  window.addEventListener('error', function (ev) {
    if (ev.target && ev.target !== window) return; // resource load errors: skip
    report({ kind: 'js', msg: ev.message, url: path(ev.filename), line: ev.lineno, col: ev.colno, stack: stack(ev.error) });
  });
  window.addEventListener('unhandledrejection', function (ev) {
    var r = ev.reason;
    report({ kind: 'promise', msg: r && r.message ? r.message : String(r), stack: stack(r) });
  });
  // CSP: the blocked origin (or inline / eval) and the directive. Not pm's, so skipped: violations caused by browser
  // extensions, and WebKit's own inline styling of form controls (a <select> on any page with style-src 'self')
  var FORM = /^(SELECT|OPTION|INPUT|TEXTAREA|BUTTON)$/;
  document.addEventListener('securitypolicyviolation', function (ev) {
    var src = String(ev.sourceFile || ''), b = String(ev.blockedURI || ''),
        dir = String(ev.effectiveDirective || ev.violatedDirective || '');
    if (/^[a-z-]*extension:/.test(src) || /^[a-z-]*extension:/.test(b)) return;
    var el = ev.target && String(ev.target.tagName || '');
    if (/^style-src/.test(dir) && (!b || b === 'inline') && FORM.test(el || '')) return;
    report({ kind: 'csp', msg: 'Refused ' + dir + ': ' +
             (/^https?:/.test(b) ? origin(b) : b || 'inline'), url: src ? path(src) : '', line: ev.lineNumber,
             col: ev.columnNumber });
  });
  document.addEventListener('htmx:responseError', function (ev) {
    var d = ev.detail || {}, xhr = d.xhr || {}, pi = d.pathInfo || {};
    report({ kind: 'htmx', status: xhr.status, url: path(pi.requestPath || (d.requestConfig || {}).path),
             method: (d.requestConfig || {}).verb, msg: 'htmx response error' });
  });
  document.addEventListener('htmx:sendError', function (ev) {
    var d = ev.detail || {}, pi = d.pathInfo || {};
    report({ kind: 'htmx', status: 0, url: path(pi.requestPath || (d.requestConfig || {}).path),
             method: (d.requestConfig || {}).verb, msg: 'htmx send error (network)' });
  });

  if (window.fetch) {
    var orig = window.fetch;
    window.fetch = function (input, init) {
      var url = typeof input === 'string' ? input : (input && input.url) || '';
      var method = (init && init.method) || (input && input.method) || 'GET';
      var self = path(url) === ENDPOINT;
      return orig.apply(this, arguments).then(function (resp) {
        if (!self && resp && resp.status >= 400) {
          report({ kind: 'fetch', status: resp.status, url: path(url), method: method, msg: 'HTTP ' + resp.status });
        }
        return resp;
      }, function (err) {
        // an aborted request or one cut by leaving the page is not an error
        if (!self && !leaving && !(err && err.name === 'AbortError')) {
          report({ kind: 'fetch', status: 0, url: path(url), method: method, msg: err && err.message });
        }
        throw err;
      });
    };
  }
})();
