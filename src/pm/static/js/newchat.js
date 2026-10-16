/* New Chat (agent header, next to Builder). Sent with fetch, not htmx: the request waits for the pane's new agent
   id and the page is left for the new agent right after, so htmx's own swap/abort handling does not fit. Flow: in-page
   modal (dialog.js) -> POST /a/<id>/newchat -> follow its HX-Redirect to the NEW agent (/a/<new id>, with
   ?embed=1 inside the drawer iframe; agentmode.js then tells the drawer the new id for its #chat= hash). */
(function () {
  'use strict';
  document.addEventListener('click', function (e) {
    var b = e.target.closest && e.target.closest('form[data-newchat] button[type=submit]');
    if (!b) return;
    e.preventDefault(); e.stopPropagation();
    var form = b.form;
    if (!window.pmConfirm || b.disabled) return;
    window.pmConfirm({ title: form.getAttribute('data-confirm') || 'Start a new chat?', confirmLabel: 'New Chat', danger: true }).then(function (ok) {
      if (!ok) return;
      var label = b.textContent;
      window.pmNewChat = true;  // agentmode.js must not reload this page into the old id's ended view meanwhile
      b.disabled = true; b.textContent = 'Starting…';
      window.pm.fetch(form.getAttribute('action'), {
        method: 'POST', credentials: 'same-origin', cache: 'no-store',
        headers: { 'HX-Request': 'true', 'HX-Current-URL': location.href, 'Content-Type': 'application/x-www-form-urlencoded' },
        body: new URLSearchParams(new FormData(form)).toString()
      }).then(function (r) {
        var to = r.headers.get('HX-Redirect');
        if (r.ok && to && /^\/a\/[A-Za-z0-9._-]+(\?embed=1)?$/.test(to)) { location.replace(to); return; }
        return r.text().then(function (t) { window.pm.notify(t, { html: true, target: window.pm.composer && window.pm.composer.slot(form) }); });
      }).catch(function () { window.pm.notify('New chat failed (network).', { kind: 'err', target: window.pm.composer && window.pm.composer.slot(form) }); })
        .then(function () { window.pmNewChat = false; b.disabled = false; b.textContent = label; });
    });
  }, true);
})();
