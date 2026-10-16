"""static/js/client-errors.js run in node against a tiny fake window: which failed fetches, errors and CSP
violations are reported, and with what."""
import json
import shutil
import subprocess
from pathlib import Path

import pytest

JS = Path(__file__).resolve().parents[1] / "src/pm/static/js/client-errors.js"

node = pytest.mark.skipif(shutil.which("node") is None, reason="node not installed")

HARNESS = r"""
const L = {}, D = {}, posted = [];
globalThis.location = { href: 'https://x/a/b', pathname: '/a/b' };
globalThis.document = { addEventListener(t, f) { (D[t] = D[t] || []).push(f); } };
let fail = null;
globalThis.window = {
  addEventListener(t, f) { (L[t] = L[t] || []).push(f); },
  fetch(url) { return fail ? Promise.reject(fail) : Promise.resolve({ status: 200 }); },
  pm: { csrf: () => 't', fetch(url, o) { posted.push(JSON.parse(o.body)); return Promise.resolve({ status: 204 }); } },
};
function fire(t, ev) { (L[t] || []).forEach((f) => f(ev || {})); }
function fireDoc(t, ev) { (D[t] || []).forEach((f) => f(ev)); }
eval(require('fs').readFileSync(process.argv[1], 'utf8'));
async function get(url, err) { fail = err; try { await window.fetch(url); } catch (e) { /* rethrown */ } }
function named(n, m) { const e = new Error(m); e.name = n; return e; }
"""


def run(code: str):
    out = subprocess.run(["node", "-e", HARNESS + "(async () => {" + code + "})();", str(JS)],
                         capture_output=True, text=True, check=True)
    return json.loads(out.stdout)


@node
def test_network_failure_reported_abort_and_unload_not():
    res = run(r"""
await get('/x/net', new TypeError('Load failed'));
await get('/x/abort', named('AbortError', 'The operation was aborted.'));
fire('pagehide');
await get('/x/leaving', new TypeError('Load failed'));
fire('pageshow');
await get('/x/back', new TypeError('Failed to fetch'));
console.log(JSON.stringify(posted.map((p) => [p.kind, p.url, p.msg])));
""")
    assert res == [["fetch", "/x/net", "Load failed"], ["fetch", "/x/back", "Failed to fetch"]]


@node
def test_csp_violation_reports_directive_and_blocked_origin_only():
    res = run(r"""
fireDoc('securitypolicyviolation', { blockedURI: 'https://avatars.example.test/u/1?s=40', effectiveDirective: 'img-src',
  sourceFile: 'https://x/static/js/a.js?v=1', lineNumber: 3, columnNumber: 9 });
fireDoc('securitypolicyviolation', { blockedURI: 'inline', violatedDirective: 'script-src-elem', sourceFile: '' });
fireDoc('securitypolicyviolation', { blockedURI: 'https://e.example.test/x', effectiveDirective: 'img-src',
  sourceFile: 'chrome-extension://abc/content.js' });
fireDoc('securitypolicyviolation', { blockedURI: 'inline', effectiveDirective: 'style-src-elem', sourceFile: '',
  target: { tagName: 'SELECT' } });
fireDoc('securitypolicyviolation', { blockedURI: 'inline', effectiveDirective: 'style-src-attr', sourceFile: '',
  target: { tagName: 'DIV' } });
console.log(JSON.stringify(posted.map((p) => [p.kind, p.msg, p.url, p.line, p.col])));
""")
    assert res == [["csp", "Refused img-src: https://avatars.example.test", "/static/js/a.js", 3, 9],
                   ["csp", "Refused script-src-elem: inline", "", None, None],
                   ["csp", "Refused style-src-attr: inline", "", None, None]]  # no extension / WebKit <select> noise


@node
def test_js_error_and_rejection_send_a_capped_stack():
    res = run(r"""
fire('error', { message: 'boom', filename: 'https://x/static/js/p.js', lineno: 1, colno: 2,
  error: { stack: 'Error: boom\n at f (p.js:1:2)' + 'x'.repeat(3000) } });
fire('unhandledrejection', { reason: { message: 'nope', stack: 'Error: nope' } });
fire('unhandledrejection', { reason: 'plain' });
console.log(JSON.stringify(posted.map((p) => [p.kind, p.msg, (p.stack || '').length, (p.stack || '').slice(0, 11)])));
""")
    assert res == [["js", "boom", 1500, "Error: boom"], ["promise", "nope", 11, "Error: nope"], ["promise", "plain", 0, ""]]
