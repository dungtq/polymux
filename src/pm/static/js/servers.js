/* Settings > Servers: "Pin host key" fetches the server's key fingerprint (POST, read-only), shows it in the
   in-page pmConfirm dialog, and only after the user agrees fills the hidden fp field and submits the pin
   form. The server re-scans and pins only when the fingerprint is still the one the user confirmed.
   No native popups. External file (strict CSP). */
(function () {
  'use strict';
  document.addEventListener('click', function (e) {
    var b = e.target && e.target.closest && e.target.closest('button[data-hk-scan]');
    if (!b || !window.pmConfirm) return;
    e.preventDefault();
    var f = b.form, body = new URLSearchParams();
    body.set('csrf', window.pm.csrf());
    b.disabled = true;
    window.pm.fetch(b.getAttribute('data-hk-scan'), { method: 'POST', body: body, credentials: 'same-origin',
                                            headers: { 'Content-Type': 'application/x-www-form-urlencoded' } })
      .then(function (r) { return r.json(); })
      .then(function (j) {
        if (!j || !j.fp) return window.pmConfirm({ title: 'Host key scan failed', body: (j && j.error) || 'No answer.',
                                                   confirmLabel: 'OK' }).then(function () { return null; });
        return window.pmConfirm({ title: 'Pin this host key for ' + (b.getAttribute('data-hk-name') || j.host) + '?',
                                  body: j.host + (j.port !== 22 ? ':' + j.port : '') + ' presents ed25519 ' + j.fp +
                                        '. Compare it with the server (ssh-keygen -lf /etc/ssh/ssh_host_ed25519_key.pub) before you agree.',
                                  confirmLabel: 'Pin' }).then(function (ok) { return ok ? j.fp : null; });
      })
      .then(function (fp) {
        if (!fp) return;
        f.querySelector('input[name=fp]').value = fp;
        if (f.requestSubmit) f.requestSubmit(); else f.submit();
      })
      .catch(function () {})
      .then(function () { b.disabled = false; });
  });
})();
