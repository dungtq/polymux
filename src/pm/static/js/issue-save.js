/* Composer "Save as issue" (item in the Send caret menu, built by snippets.js) and the `#draft=` prefill.
 * Save: first non-empty line = title, the rest = body, finished image chips = images; POST to
 * /a/<id>/issue (the issue lands in the agent's project). Never sent to the agent. On success the box and
 * chips clear like a send and the composer's notice line links the issue; on failure the text and chips stay.
 * Draft: /a/<id>#draft=<urlencoded text> fills an empty box (Send to agent from an issue), never sends.
 * Per composer root ([data-cmp-*] hooks). External file only (strict CSP); after composer-attach.js (pm.composerAttach). */
(function () {
  'use strict';
  var pm = window.pm, at = pm.composerAttach || {};
  var IMG = /\.(?:png|jpg|gif|webp)$/, DRAFT_MAX = 4000, WAIT_MS = 60000;
  // ---- pure helpers (exported for tests) ----
  function split(text) {
    var lines = String(text || '').split('\n'), i = 0;
    while (i < lines.length && !lines[i].trim()) i++;
    if (i >= lines.length) return { title: '', body: '' };
    return { title: lines[i].trim(), body: lines.slice(i + 1).join('\n').replace(/^\s*\n/, '').replace(/\s+$/, '') };
  }
  function issueUrl(action) {
    var a = String(action || '');
    return /\/message$/.test(a) ? a.replace(/\/message$/, '/issue') : '';
  }
  // `#draft=<text>&chat=...` -> { text, hash: what is left of the hash ('' when nothing) }; null when no draft
  function draftFrom(hash) {
    var h = String(hash || '').replace(/^#/, '');
    if (!h) return null;
    var parts = h.split('&'), text = null, rest = [];
    parts.forEach(function (p) {
      if (text === null && p.indexOf('draft=') === 0) {
        try { text = decodeURIComponent(p.slice(6).replace(/\+/g, ' ')); } catch (err) { text = ''; }
      } else rest.push(p);
    });
    if (text === null) return null;
    return { text: text.slice(0, DRAFT_MAX), hash: rest.length ? '#' + rest.join('&') : '' };
  }
  var root = null;  // the composer whose "Save as issue" was clicked
  function slot() { return root && root.querySelector('[data-cmp-flash]'); }  // the composer's notice line (pm.notify)
  function err(msg) { if (slot()) pm.notify(msg, { kind: 'err', target: slot() }); }
  function chips(r) { return at.chipList ? at.chipList(r) : []; }
  // Same clearing as a send (chat-send.js htmx:beforeRequest): chips go, the "File attached." line goes,
  // the box empties; syncRequired fires `input`, so the draft store and the send button follow.
  function clear(r) {
    var ta = r.querySelector('[data-cmp-input]'), f = r.querySelector('[data-cmp-flash]');
    chips(r).forEach(function (c) { c.remove(); });
    if (f && f.querySelector('[data-image-path]')) f.innerHTML = '';
    if (ta) ta.value = '';
    if (at.syncRequired) at.syncRequired(r);
    else if (ta) ta.dispatchEvent(new Event('input', { bubbles: true }));
  }
  function uploading(r) { return chips(r).some(function (c) { return c.classList.contains('is-uploading'); }); }
  function waitUploads(r) {
    return new Promise(function (resolve) {
      if (!uploading(r)) { resolve(true); return; }
      if (slot()) pm.notify('Waiting for uploads...', { ttl: 0, target: slot() });
      var t0 = Date.now(), iv = window.setInterval(function () {
        if (uploading(r) && Date.now() - t0 < WAIT_MS) return;
        window.clearInterval(iv);
        resolve(!uploading(r));
      }, 200);
    });
  }
  function done(j) {
    var url = typeof j.url === 'string' && /^\/(?!\/)/.test(j.url) ? j.url
      : '/p/' + encodeURIComponent(j.project || '') + '#issue=' + encodeURIComponent(j.num);
    var top = window.self !== window.top ? ' target="_top"' : '';  // embedded agent page: open in the main window
    // .flash-link: a plain link in the line (base.css boxes every element child of .flash; chat.css undoes it)
    if (slot()) pm.notify('<p class="flash ok">Issue #' + pm.esc(j.num) + ' saved. <a class="flash-link" href="' + pm.esc(url) + '"' + top + '>Open it</a></p>', { html: true, target: slot() });
  }
  var busy = false;
  function save(btn, r) {
    var form = r && r.querySelector('[data-cmp-form]'), ta = r && r.querySelector('[data-cmp-input]');
    if (!form || !ta || busy) return;
    root = r;
    var url = issueUrl(form.getAttribute('action'));
    if (!url) { err('Save as issue is not available here.'); return; }
    if (!split(ta.value).title) { err('Type a title first: the first line becomes the issue title.'); ta.focus(); return; }
    busy = true; btn.disabled = true; btn.setAttribute('aria-busy', 'true');
    function end() { busy = false; btn.disabled = false; btn.removeAttribute('aria-busy'); }
    waitUploads(r).then(function (ok) {
      if (!ok) { err('Still uploading...'); end(); return; }
      var paths = at.chipsReady ? at.chipsReady(r).map(function (c) { return c.dataset.path; }) : [];
      if (paths.some(function (p) { return !IMG.test(p); })) {
        err('Only images can be saved with an issue; remove the other attachments.'); end(); return;
      }
      var text = ta.value, body = new FormData();
      body.set('text', text); body.set('images', paths.join('\n'));
      return pm.fetch(url, { method: 'POST', body: body, csrf: 'header' })
        .then(function (r) { return r.text().then(function (t) { return { ok: r.ok, status: r.status, t: t }; }); })
        .then(function (res) {
          var j = null;
          try { j = JSON.parse(res.t); } catch (e2) { j = null; }
          if (res.ok && j && j.ok) {
            if (ta.value === text) clear(r);  // typed on while saving: keep the new text
            done(j);
          } else err((j && j.error) || ('Save as issue failed (' + res.status + ').'));
        })
        .catch(function () { err('Save as issue failed (network).'); })
        .then(end);
    });
  }
  document.addEventListener('click', function (e) {
    var b = e.target && e.target.closest && e.target.closest('[data-issue-save]');
    if (b && !b.disabled) save(b, b.closest('[data-composer]'));
  });
  // ---- #draft= prefill (after composer.js restored its stored draft: it runs first) ----
  function prefill() {
    var d = draftFrom(location.hash), ta = document.querySelector('[data-composer][data-agent] [data-cmp-input]');
    root = ta && ta.closest('[data-composer]');
    if (!d) return;
    try { history.replaceState(null, '', location.pathname + location.search + d.hash); } catch (err2) { /* ignore */ }
    if (!ta || !d.text) return;
    if (ta.value.trim()) { err('The message box already has text; the draft was not inserted.'); return; }
    ta.value = d.text;
    ta.dispatchEvent(new Event('input', { bubbles: true }));
    ta.focus();
    try { ta.setSelectionRange(ta.value.length, ta.value.length); } catch (err3) { /* ignore */ }
  }
  if (document.readyState === 'loading') document.addEventListener('DOMContentLoaded', prefill); else prefill();
  pm.issueSave = { split: split, issueUrl: issueUrl, draftFrom: draftFrom, clear: clear, save: save };
})();
