/* "Continue..." under a reply: an in-page popup (dialog.js pmConfirm with content + collect) instead of posting
   at once. Choose the prompt: the default message, or a new one typed here with canned chips, and where the new
   chat opens (a new tmux window, or a split pane here), then POST /a/<id>/continue with
   mode=predefined|custom, where=window|pane and seq (or the reply text) / text. The page stays; the answer is a
   flash fragment shown in #flash. The last choice is remembered in localStorage (convenience only).
   The new prompt is the shared composer (composer("continue"), cloned from <template id="cmp-continue">: autosize, the
   .cbox look, Ctrl/Cmd+Enter = Start through pm.composer.init(root, {onSend})); Start stays off while it is empty. */
(function () {
  'use strict';
  var CHIPS = ['Continue where you left off.', 'Go ahead.', 'Run the tests and fix any failures.', 'Check BACKLOG.md for open items.'];
  var KEY = 'pm.continue.choice';
  function el(tag, cls, text) { var n = document.createElement(tag); if (cls) n.className = cls; if (text != null) n.textContent = text; return n; }
  function load() {
    try { var v = JSON.parse(localStorage.getItem(KEY) || '{}'); return v && typeof v === 'object' ? v : {}; } catch (e) { return {}; }
  }
  function save(v) { try { localStorage.setItem(KEY, JSON.stringify(v)); } catch (e) { /* private mode */ } }
  function replyText(el_) {
    var bub = el_.closest('.chat-bubble'), copy = bub && bub.querySelector('.chat-meta .chat-copy[data-raw]');
    return copy ? (copy.getAttribute('data-raw') || '') : '';
  }
  function radio(group, value, label, checked, hint) {
    var row = el('label', 'cpop-opt'), inp = el('input');
    inp.type = 'radio'; inp.name = 'cpop-' + group; inp.value = value; inp.checked = checked;
    row.appendChild(inp);
    var txt = el('span', 'cpop-t'); txt.appendChild(el('span', 'cpop-l', label));
    if (hint) txt.appendChild(el('span', 'cpop-h small muted', hint));
    row.appendChild(txt);
    return { row: row, inp: inp };
  }
  function promptBox() {  // the composer from the page's template (agent page, Builder); a bare textarea without one
    var t = document.getElementById('cmp-continue'), root = t && t.content && t.content.firstElementChild;
    root = root ? root.cloneNode(true) : el('div', 'composer');
    var ta = root.querySelector('[data-cmp-input]') || root.appendChild(el('textarea'));
    ta.classList.add('cpop-ta');
    return { root: root, ta: ta };
  }
  function build(saved) {
    var box = el('div', 'cpop'), custom = saved.mode === 'custom', pane = saved.where === 'pane';
    var g1 = el('fieldset', 'cpop-g'), g2 = el('fieldset', 'cpop-g');
    g1.appendChild(el('legend', 'cpop-lg small muted', 'Prompt'));
    g2.appendChild(el('legend', 'cpop-lg small muted', 'Where'));
    var pre = radio('mode', 'predefined', 'Use the default message', !custom,
      'This reply is handed over and the new agent first asks what to do next.');
    var cus = radio('mode', 'custom', 'Write a new prompt', custom);
    var pb = promptBox(), ta = pb.ta;
    var chips = el('div', 'cpop-chips'), chipBtns = [];
    CHIPS.forEach(function (c) {
      var b = el('button', 'small cpop-chip', c); b.type = 'button'; chips.appendChild(b); chipBtns.push(b);
      b.addEventListener('click', function () { ta.value = c; ta.focus(); ta.dispatchEvent(new Event('input', { bubbles: true })); });
    });
    g1.appendChild(pre.row); g1.appendChild(cus.row); g1.appendChild(pb.root); g1.appendChild(chips);
    var win = radio('where', 'window', 'New tmux window', !pane), spl = radio('where', 'pane', 'This window (split pane)', pane);
    g2.appendChild(win.row); g2.appendChild(spl.row);
    box.appendChild(g1); box.appendChild(g2);
    function isCustom() { return cus.inp.checked; }
    function okBtn() { var d = box.closest('dialog'); return d && d.querySelector('[data-dlg-ok]'); }
    function sync() {
      ta.disabled = !isCustom(); chipBtns.forEach(function (b) { b.disabled = !isCustom(); });
      var ok = okBtn();
      if (ok) ok.disabled = isCustom() && !ta.value.trim();
    }
    [pre.inp, cus.inp].forEach(function (i) { i.addEventListener('change', function () { sync(); if (isCustom()) ta.focus(); }); });
    ta.addEventListener('input', sync);
    function mount() {  // once the dialog holds it: the composer's send key (Ctrl/Cmd+Enter) presses Start
      var c = window.pm && window.pm.composer;
      if (c && c.init) c.init(pb.root, { onSend: function () { var ok = okBtn(); if (ok && !ok.disabled) ok.click(); return false; } });
    }
    return {
      box: box, sync: sync, mount: mount, focus: function () { if (isCustom()) ta.focus(); },
      collect: function () { return { mode: isCustom() ? 'custom' : 'predefined', where: spl.inp.checked ? 'pane' : 'window', text: ta.value.trim() }; }
    };
  }

  document.addEventListener('click', function (e) {
    var cb = e.target.closest && e.target.closest('form.cm-cont button');
    if (!cb || cb.disabled) return;
    e.preventDefault(); e.stopPropagation();
    var form = cb.closest('form.cm-cont'), text = replyText(cb).trim();
    var seq = form.querySelector('input[name=seq]');
    if (!text && !seq) { window.pm.notify('Nothing to send.', { kind: 'err' }); return; }
    if (!window.pmConfirm) return;
    var ui = build(load());
    cb.disabled = true;
    var p = window.pmConfirm({ title: 'Continue in a new chat', confirmLabel: 'Start', content: ui.box, collect: ui.collect });
    ui.mount(); ui.sync(); ui.focus();  // pmConfirm has mounted the dialog synchronously
    p.then(function (v) {
      if (!v) { cb.disabled = false; return; }
      save({ mode: v.mode, where: v.where });
      var params = { csrf: window.pm.csrf(), mode: v.mode, where: v.where };
      if (v.mode === 'custom') params.text = v.text;
      else if (seq) params.seq = seq.value;
      else params.text = text;
      window.pm.fetch(form.getAttribute('action'), {
        method: 'POST', credentials: 'same-origin', cache: 'no-store',
        headers: { 'HX-Request': 'true', 'HX-Current-URL': location.href, 'Content-Type': 'application/x-www-form-urlencoded' },
        body: new URLSearchParams(params).toString()
      }).then(function (r) { return r.text().then(function (t) { window.pm.notify(t, { html: true }); }); })
        .catch(function () { window.pm.notify('Continue failed (network).', { kind: 'err' }); })
        .then(function () { cb.disabled = false; });
    });
  }, true);
})();
