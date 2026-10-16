/* Composer transport data-send="htmx" (agent page, Builder): the message leaves the box at once; the POST stores it in
 * pm's outbox and answers with its bubble (_outbox.html), which chat-items.js shows below the running turn (state
 * Saved -> ... -> In transcript). On failure the text and the attachment chips go back into the box. Slash commands
 * keep their notice answer. Also the quiet send hint ([data-cmp-hint]) after a send.
 * Per root, through the [data-cmp-*] hooks; uses pm.composerAttach (composer-attach.js). External file (strict CSP). */
(function () {
  'use strict';
  var pm = window.pm, at = pm.composerAttach;
  var outFor = new WeakMap();  // xhr -> what to put back when the send fails
  function rootOf(el) { return el && el.closest ? el.closest('[data-composer][data-send="htmx"]') : null; }
  function sendRoot(el) { return el && el.matches && el.matches('[data-cmp-form]') ? rootOf(el) : null; }
  function synced(root, ta) { if (at) at.syncRequired(root); else ta.dispatchEvent(new Event('input', { bubbles: true })); }
  // Paths live on the chips only; add them to the outgoing text here.
  document.addEventListener('htmx:configRequest', function (e) {
    var root = sendRoot(e.detail && e.detail.elt);
    if (!root || !at) return;
    var paths = at.chipsReady(root).map(function (c) { return c.dataset.path; });
    if (!paths.length) return;
    var ta = root.querySelector('[data-cmp-input]'), typed = ta ? ta.value.replace(/\s+$/, '') : '';
    e.detail.parameters[ta ? ta.name : 'text'] = (typed ? typed + '\n' : '') + paths.join('\n');
  });
  document.addEventListener('htmx:beforeRequest', function (e) {
    var root = sendRoot(e.detail && e.detail.elt);
    if (!root) return;
    var ta = root.querySelector('[data-cmp-input]'), flash = root.querySelector('[data-cmp-flash]');
    var all = at ? at.chipList(root) : [];
    if (all.some(function (c) { return c.classList.contains('is-uploading'); })) {
      e.preventDefault();
      pm.notify('Still uploading...', { kind: 'err', target: flash });
      return;
    }
    if (!ta || (!ta.value.trim() && !all.length)) return;
    all.forEach(function (c) { c.remove(); });
    if (flash && flash.querySelector('[data-image-path]')) flash.textContent = '';  // "File attached." is done once sent
    outFor.set(e.detail.xhr, { text: ta.value, ta: ta, chips: all, bar: root.querySelector('[data-cmp-chips]'), root: root });
    ta.value = '';
    synced(root, ta);
  });
  // the stored message comes back as its bubble: chat-items.js places it (not the notice line)
  document.addEventListener('htmx:beforeSwap', function (e) {
    var d = e.detail, el = d && d.requestConfig && d.requestConfig.elt;
    if (!sendRoot(el) || !d.xhr || d.xhr.status >= 300) return;
    var body = d.xhr.responseText || '';
    if (body.indexOf('data-ob=') < 0 || !pm.chatItems) return;
    if (pm.chatItems.addOutbox(body)) d.shouldSwap = false;
  });
  document.addEventListener('htmx:afterRequest', function (e) {
    var d = e.detail, rec = d && d.xhr && outFor.get(d.xhr);
    if (!rec) return;
    outFor.delete(d.xhr);
    if (d.successful) { showHint(rec.root); return; }
    if (!rec.ta.value) rec.ta.value = rec.text;
    if (rec.bar) rec.chips.forEach(function (c) { rec.bar.appendChild(c); });
    synced(rec.root, rec.ta);
  });

  // ---- quiet reminder hint after a send (per viewer; never part of the message) ----
  var HINT_KEY = 'pm.hideSendHint', hintTimer = 0;
  function hideHint(h) { if (h) h.hidden = true; window.clearTimeout(hintTimer); }
  function showHint(root) {
    var h = root.querySelector('[data-cmp-hint]');
    if (!h || pm.store.get(HINT_KEY) === '1') return;
    h.hidden = false;
    window.clearTimeout(hintTimer);
    hintTimer = window.setTimeout(function () { hideHint(h); }, 8000);
  }
  document.addEventListener('click', function (e) {
    var b = e.target.closest && e.target.closest('[data-hint-close]');
    if (b) hideHint(b.closest('[data-cmp-hint]'));
  });
  document.addEventListener('change', function (e) {
    var t = e.target;
    if (!t || !t.matches || !t.matches('[data-hint-off]')) return;
    if (t.checked) { pm.store.set(HINT_KEY, '1'); hideHint(t.closest('[data-cmp-hint]')); } else pm.store.remove(HINT_KEY);
  });
})();
