/* /a/{id} (and /a/{id}/builder) is one permanent URL with two server-side views (live agent / ended record). This
 * watches GET /a/{id}/mode and reloads the page (same URL, hash kept) when the view changes, so
 * Exit and Resume (also the ended composer's "Resume and send") need no manual F5. Forms marked data-transition show
 * "Exiting..." / "Resuming..." on their button (aria-busy, so composer.js leaves it alone) until the page switches, or a
 * timeout message. Read-only polling; external file (strict CSP). */
(function () {
  'use strict';
  var head = document.querySelector('.agent-head[data-mode][data-agent], #builder[data-mode][data-agent]');
  if (!head || !window.fetch) return;
  var cur = head.getAttribute('data-mode'), id = head.getAttribute('data-agent');
  // framed in the drawer (?embed=1): tell the parent which agent this page really is, so its #chat= hash,
  // pills and links follow when the id changes (New Chat starts a new session = new agent id).
  if (document.body.classList.contains('embed') && window.parent !== window) {
    try { window.parent.postMessage({ pm: 'drawer', act: 'at', id: id, path: location.pathname, pane: head.getAttribute('data-pane') || '' }, location.origin); } catch (e) { /* ignore */ }
  }
  var IDLE_MS = 10000, FAST_MS = 1000, LIMIT_MS = 60000;
  var timer = null, busy = false, fastUntil = 0, pending = null;

  function restore() {
    if (!pending) return;
    pending.btn.disabled = false;
    pending.btn.removeAttribute('aria-busy');
    pending.btn.textContent = pending.label;
    if (window.pm.composer && window.pm.composer.sync) window.pm.composer.sync(pending.btn);  // its labels follow the box again
    pending = null; fastUntil = 0;
  }
  function check() {
    if (busy || document.hidden) return;
    busy = true;
    window.pm.fetch('/a/' + encodeURIComponent(id) + '/mode', { credentials: 'same-origin', cache: 'no-store' })
      .then(function (r) { return r.ok ? r.json() : null; })
      .then(function (j) {
        busy = false;
        if (j && j.mode && j.mode !== 'gone' && j.mode !== cur && !window.pmNewChat) { location.reload(); return; }
        if (pending && Date.now() > fastUntil) {
          var what = pending.kind;
          restore();
          window.pm.notify(what === 'Exiting' ? 'Still running after 60 s. The agent may be busy; check it.'
                : 'Not running after 60 s. Check the tmux window or try Resume again.', { kind: 'err' });
        }
      })
      .catch(function () { busy = false; });
  }
  function schedule() {
    if (timer) clearTimeout(timer);
    timer = setTimeout(function () { check(); schedule(); }, pending ? FAST_MS : IDLE_MS);
  }

  ['pm:agent_state', 'pm:agent_added', 'pm:agent_removed'].forEach(function (t) {
    document.addEventListener(t, check);
  });
  document.addEventListener('visibilitychange', function () { if (!document.hidden) check(); });

  document.addEventListener('htmx:beforeRequest', function (e) {
    var f = e.detail && e.detail.elt;
    if (!f || !f.matches || !f.matches('form[data-transition]')) return;
    var btn = f.querySelector('button[type=submit]');
    if (!btn || pending) return;
    pending = { btn: btn, label: btn.textContent, kind: f.getAttribute('data-transition').replace(/\W.*$/, '') };
    // htmx disables nothing by itself; keep the label and block double clicks until the outcome
    btn.disabled = true;
    btn.setAttribute('aria-busy', 'true');
    btn.textContent = f.getAttribute('data-transition').replace(/&hellip;|…/, '…');
    fastUntil = Date.now() + LIMIT_MS;
    schedule();
  });
  document.addEventListener('htmx:afterRequest', function (e) {
    var f = e.detail && e.detail.elt;
    if (!f || !f.matches || !f.matches('form[data-transition]') || !pending) return;
    if (!e.detail.successful) { restore(); schedule(); return; }  // refused: flash already shown
    check();
  });
  schedule();
})();
