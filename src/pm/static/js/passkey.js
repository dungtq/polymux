/* WebAuthn glue for /passkey/{register,auth}/{begin,finish}. External file only. */
(function () {
  'use strict';

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
    return window.pm.fetch(url, {
      method: 'POST',
      credentials: 'same-origin', csrf: 'header',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(body || {})
    }).then(function (r) {
      return r.json().catch(function () { return {}; }).then(function (j) {
        if (!r.ok) throw new Error(j.error || j.detail || ('HTTP ' + r.status));
        return j;
      });
    });
  }
  function say(target, msg, ok) {
    if (!target) return;
    target.textContent = msg;
    target.className = (target.className || '').replace(/\b(ok|err)\b/g, '').trim() + (ok ? ' ok' : ' err');
  }

  function creationOptions(o) {
    var pk = o.publicKey || o;
    pk.challenge = b64uToBuf(pk.challenge);
    pk.user.id = b64uToBuf(pk.user.id);
    (pk.excludeCredentials || []).forEach(function (c) { c.id = b64uToBuf(c.id); });
    return { publicKey: pk };
  }
  function requestOptions(o) {
    var pk = o.publicKey || o;
    pk.challenge = b64uToBuf(pk.challenge);
    (pk.allowCredentials || []).forEach(function (c) { c.id = b64uToBuf(c.id); });
    return { publicKey: pk };
  }
  function attestationJSON(cred) {
    var r = cred.response;
    return {
      id: cred.id, rawId: bufToB64u(cred.rawId), type: cred.type,
      authenticatorAttachment: cred.authenticatorAttachment || null,
      clientExtensionResults: cred.getClientExtensionResults ? cred.getClientExtensionResults() : {},
      response: {
        clientDataJSON: bufToB64u(r.clientDataJSON),
        attestationObject: bufToB64u(r.attestationObject),
        transports: r.getTransports ? r.getTransports() : []
      }
    };
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

  function register(target) {
    var nameEl = document.getElementById('passkey-name');
    var name = nameEl ? nameEl.value : '';
    return post('/passkey/register/begin').then(function (opts) {
      return navigator.credentials.create(creationOptions(opts));
    }).then(function (cred) {
      return post('/passkey/register/finish', { credential: attestationJSON(cred), name: name });
    }).then(function () { say(target, 'Passkey registered.', true); document.body.dispatchEvent(new Event('passkey-registered')); });
  }

  function authenticate(target) {
    return post('/passkey/auth/begin').then(function (opts) {
      return navigator.credentials.get(requestOptions(opts));
    }).then(function (cred) {
      return post('/passkey/auth/finish', { credential: assertionJSON(cred) });
    }).then(function (res) {
      if (!res.unlocked) throw new Error('not unlocked');
      say(target, 'Unlocked.', true);
      setTimeout(function () { window.location.reload(); }, 400);
    });
  }

  document.addEventListener('click', function (e) {
    var btn = e.target.closest && e.target.closest('[data-passkey]');
    if (!btn) return;
    var target = document.getElementById(btn.getAttribute('data-target') || '');
    if (!window.PublicKeyCredential || !navigator.credentials) {
      say(target, 'Passkeys are not supported here.', false);
      return;
    }
    var run = btn.getAttribute('data-passkey') === 'register' ? register : authenticate;
    btn.disabled = true;
    run(target).catch(function (err) {
      say(target, 'Passkey failed: ' + (err && err.message ? err.message : 'error'), false);
    }).then(function () { btn.disabled = false; });
  });
})();
