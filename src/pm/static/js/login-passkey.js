/* Passkey sign-in for /login. External file only (CSP forbids inline script). */
(function () {
  'use strict';
  var btn = document.getElementById('passkey-login');
  var msg = document.getElementById('passkey-msg');
  if (!btn) return;
  if (!window.PublicKeyCredential || !navigator.credentials) {
    btn.hidden = true;
    return;
  }

  function b64uToBuf(s) {
    var p = s.replace(/-/g, '+').replace(/_/g, '/');
    while (p.length % 4) p += '=';
    var bin = atob(p), out = new Uint8Array(bin.length);
    for (var i = 0; i < bin.length; i++) out[i] = bin.charCodeAt(i);
    return out.buffer;
  }
  function bufToB64u(buf) {
    var bytes = new Uint8Array(buf), bin = '';
    for (var i = 0; i < bytes.length; i++) bin += String.fromCharCode(bytes[i]);
    return btoa(bin).replace(/\+/g, '-').replace(/\//g, '_').replace(/=+$/, '');
  }
  function post(url, body) {
    return fetch(url, {
      method: 'POST',
      credentials: 'same-origin',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(body || {})
    }).then(function (r) {
      return r.json().catch(function () { return {}; }).then(function (j) {
        if (!r.ok) throw new Error(j.error || j.detail || ('HTTP ' + r.status));
        return j;
      });
    });
  }
  function assertionJSON(cred) {
    var r = cred.response;
    return {
      id: cred.id, rawId: bufToB64u(cred.rawId), type: cred.type,
      authenticatorAttachment: cred.authenticatorAttachment || null,
      clientExtensionResults: cred.getClientExtensionResults ? cred.getClientExtensionResults() : {},
      response: {
        clientDataJSON: bufToB64u(r.clientDataJSON),
        authenticatorData: bufToB64u(r.authenticatorData),
        signature: bufToB64u(r.signature),
        userHandle: r.userHandle ? bufToB64u(r.userHandle) : null
      }
    };
  }

  btn.addEventListener('click', function () {
    btn.disabled = true;
    if (msg) msg.textContent = '';
    post('/login/passkey/begin').then(function (o) {
      var pk = o.publicKey || o;
      pk.challenge = b64uToBuf(pk.challenge);
      (pk.allowCredentials || []).forEach(function (c) { c.id = b64uToBuf(c.id); });
      return navigator.credentials.get({ publicKey: pk });
    }).then(function (cred) {
      return post('/login/passkey/finish',
                  { credential: assertionJSON(cred), next: btn.getAttribute('data-next') || '/' });
    }).then(function (res) {
      window.location.assign(res.next || '/');
    }).catch(function (err) {
      if (msg) msg.textContent = 'Passkey sign-in failed: ' + (err && err.message ? err.message : 'error');
      btn.disabled = false;
    });
  });
})();
