/* Chat tab glue: Chat / Pane tab memory, the keypad panel, copy buttons, the refresh after a send, the Pane tab's
 * in-place updates. The composer (Enter to send, Keys toggle, ...) is composer.js. External file only (strict CSP). */
(function () {
  'use strict';
  var KEY = 'pm.agentTab';

  function init() {
    var chat = document.getElementById('tab-chat');
    var pane = document.getElementById('tab-pane');
    if (!chat || !pane) return;
    try {
      if (localStorage.getItem(KEY) === 'pane') pane.checked = true;
    } catch (e) { /* storage unavailable */ }
    [chat, pane].forEach(function (r) {
      r.addEventListener('change', function () {
        try { localStorage.setItem(KEY, pane.checked ? 'pane' : 'chat'); } catch (e) {}
      });
    });
    if (pane.checked) pane.dispatchEvent(new Event('change', { bubbles: true }));  // the composer's Keys toggle follows
  }
  if (document.readyState === 'loading') document.addEventListener('DOMContentLoaded', init);
  else init();

  // Keys panel: open by default, remembered per browser.
  function initKeys() {
    var d = document.getElementById('keypad-wrap');
    if (!d) return;
    try {
      var v = localStorage.getItem('pm.keypad');
      if (v === 'closed') d.open = false;
    } catch (e) { /* storage unavailable */ }
    d.addEventListener('toggle', function () {
      try { localStorage.setItem('pm.keypad', d.open ? 'open' : 'closed'); } catch (e) {}
    });
  }
  if (document.readyState === 'loading') document.addEventListener('DOMContentLoaded', initKeys);
  else initKeys();

  // Copy button (event delegation: the chat fragment is re-swapped every 2s).
  // The button carries the raw text in data-raw. The transient state lives here, not in the DOM,
  // and is re-applied to the fresh button after a swap.
  var copied = null, copiedTimer = 0;
  function paintCopy(b, st) {
    var l = b.querySelector('.chat-copy-l');
    b.classList.toggle('is-copied', st === 'copied');
    b.classList.toggle('is-failed', st === 'failed');
    if (l) l.textContent = st === 'copied' ? 'Copied' : st === 'failed' ? 'Copy failed' : '';
    b.setAttribute('aria-label', st === 'copied' ? 'Copied' : 'Copy message');
  }
  function paintAll() {
    document.querySelectorAll('.chat-copy').forEach(function (b) {
      var on = copied && Date.now() < copied.until && b.getAttribute('data-raw') === copied.raw;
      paintCopy(b, on ? copied.state : '');
    });
  }
  document.addEventListener('click', function (e) {
    var b = e.target.closest && e.target.closest('.chat-copy');
    if (!b) return;
    var raw = b.getAttribute('data-raw') || '';
    window.pm.copy(raw).then(function (ok) {
      copied = { raw: raw, state: ok ? 'copied' : 'failed', until: Date.now() + 1800 };
      paintAll();
      clearTimeout(copiedTimer);
      copiedTimer = setTimeout(function () { copied = null; paintAll(); }, 1800);
    });
  });
  document.addEventListener('htmx:afterSettle', function (e) {
    if (copied && e.target && e.target.id === 'chat') paintAll();
  });
  document.addEventListener('htmx:load', function (e) {
    if (copied && e.target && e.target.closest && e.target.closest('#chat, #chatlog')) paintAll();
  });

  // After a message/answer/key goes out, refresh the chat fragment.
  document.addEventListener('htmx:afterRequest', function (evt) {
    var d = evt.detail, elt = d && d.elt;
    if (!elt || !elt.matches || !d.successful) return;
    if (!elt.matches('.composer form, .chat form, .keypad form')) return;
    var ta = elt.matches('form.chat-answer') ? elt.querySelector('input[name=text]') : null;
    if (ta) { ta.value = ''; ta.dispatchEvent(new Event('input')); }
    setTimeout(function () {
      ['chat', 'pane'].forEach(function (id) {
        var c = document.getElementById(id);
        if (c && window.htmx) window.htmx.trigger(c, 'pm:refresh');
      });
    }, 600);
  });

  // --- Pane tab: stick to the bottom unless the user scrolled up ---
  var pinned = true;      // true until the user scrolls away from the bottom
  var savedTop = 0;
  var NEAR = 24;

  function paneEl() { return document.querySelector('#pane pre.pane[data-pin]'); }
  function atBottom(el) { return el.scrollTop + el.clientHeight >= el.scrollHeight - NEAR; }
  function visible(el) { return !!el && el.clientHeight > 0; }
  function jumpBtn() { return document.querySelector('#pane [data-pane-jump]'); }
  function syncJump() {
    var b = jumpBtn();
    if (b && b.hidden !== pinned) b.hidden = pinned;
  }
  function toBottom(el) {
    el.scrollTop = el.scrollHeight;
    pinned = true;
    syncJump();
  }

  document.addEventListener('scroll', function (e) {
    var el = e.target;
    if (!el || !el.matches || !el.matches('pre.pane[data-pin]') || !visible(el)) return;
    pinned = atBottom(el);
    savedTop = el.scrollTop;
    syncJump();
  }, true);

  document.addEventListener('click', function (e) {
    var b = e.target.closest && e.target.closest('[data-pane-jump]');
    if (!b) return;
    var el = paneEl();
    if (el) toBottom(el);
  });

  var draft = null;

  // Pane poll: never swap the element. Compare text; unchanged -> no DOM write;
  // changed -> rewrite the existing text node and fix scroll in the same task.
  function updatePaneInPlace(html) {
    var wrap = document.getElementById('pane');
    var el = paneEl();
    if (!wrap || !el) return false;
    var tpl = document.createElement('template');
    tpl.innerHTML = html;
    var nu = tpl.content.querySelector('pre.pane');
    if (!nu) return false;
    var text = nu.textContent;
    if (el.textContent !== text) {
      var vis = visible(el);
      var wasPinned = vis ? atBottom(el) : pinned;
      var top = el.scrollTop;
      var node = el.firstChild;
      if (node && node.nodeType === 3 && !node.nextSibling) node.nodeValue = text;
      else el.textContent = text;
      if (vis) {
        if (wasPinned) el.scrollTop = el.scrollHeight; else el.scrollTop = top;
        pinned = wasPinned;
        syncJump();
      }
    }
    var src = tpl.content.querySelector('#pane');
    var hg = src && src.getAttribute('hx-get');
    if (hg && wrap.getAttribute('hx-get') !== hg) wrap.setAttribute('hx-get', hg);
    var st = tpl.content.querySelector('#agent-state');
    var cur = document.getElementById('agent-state');
    if (st && cur && cur.innerHTML.trim() !== st.innerHTML.trim()) { cur.innerHTML = st.innerHTML; var pl = document.getElementById('agent-pill'); if (pl) { pl.title = cur.textContent.trim(); pillLabel(pl); } }
    return true;
  }

  // Heading pill (_agentname.html agent_head): "<model>, <state>" for screen readers; a model / effort switch answers
  // with the HX-Trigger pm:composer {ph, phShort, pill} (web/agentctl.py composer_event), the pill follows it.
  function pillLabel(pl) { pl.setAttribute('aria-label', pl.textContent + (pl.title ? ', ' + pl.title : '')); }
  document.addEventListener('pm:composer', function (e) {
    var x = e.detail || {}, pl = document.getElementById('agent-pill');
    if (!pl || typeof x.pill !== 'string' || !x.pill) return;
    pl.textContent = x.pill; pillLabel(pl);
    var h = pl.closest('[data-model]');
    if (h) { h.setAttribute('data-model', x.pill); h.title = h.title.replace(/^(\S+) \S+/, '$1 ' + x.pill); }
  });

  document.addEventListener('htmx:beforeSwap', function (e) {
    var d = e.detail, t = d && d.target;
    if (!t || !t.id) return;
    if (t.id === 'pane') {
      if (d.xhr && d.xhr.status === 200 && updatePaneInPlace(d.serverResponse)) {
        d.shouldSwap = false;
      }
    } else if (t.id === 'chat') {
      var inp = t.querySelector('.chat-answer input[name=text]');
      draft = inp ? { v: inp.value, focus: document.activeElement === inp,
                      s: inp.selectionStart, e: inp.selectionEnd } : null;
    }
  });

  document.addEventListener('htmx:load', function (e) {
    var t = e.target;
    if (!t || !t.matches) return;
    if (t.matches('#chat') && draft) {
      var inp = t.querySelector('.chat-answer input[name=text]');
      if (inp && draft.v) {
        inp.value = draft.v;
        if (draft.focus) {
          inp.focus();
          try { inp.setSelectionRange(draft.s, draft.e); } catch (err) {}
        }
      }
      draft = null;
    }
  });

  function onShow() {
    var el = paneEl();
    if (!el) return;
    window.requestAnimationFrame(function () {
      if (visible(el)) { if (pinned) toBottom(el); else el.scrollTop = savedTop; syncJump(); }
    });
  }
  function initPane() {
    var pr = document.getElementById('tab-pane');
    if (pr) pr.addEventListener('change', function () { if (pr.checked) onShow(); });
    var el = paneEl();
    if (visible(el)) toBottom(el);
    else syncJump();
  }
  if (document.readyState === 'loading') document.addEventListener('DOMContentLoaded', initPane);
  else initPane();

})();
