/* The one in-page modal helper. pm never uses the browser's alert/confirm/prompt popups (docs/UI-RULES.md).
   - window.pmDialog(el | id | opts, extra) -> the <dialog>. An element (or its id): opens that static dialog
     (extra = {onClose, focus}). Opts {title, body: text|Node, content: Node|[Node], foot: Node|[Node], size: 'sm'|'md'|
     'wide'|'full', iframe: src, closeX, cls, label, focus: Node, keep, onClose: fn(returnValue, dlg)} builds a
     <dialog class="pmdlg"> (removed again on close unless keep). Every dialog it shows gets: the backdrop rule (a click
     outside the box closes it), Esc (native cancel), [data-dlg-close] closes it, focus on opts.focus / [autofocus], else
     the first field, else the safe (Cancel) button, and a unique aria-labelledby id for its title.
     window.pmDialog.close(dlg, value).
   - window.pmConfirm({title, body, choices:[{label,value,danger,hint}], confirmLabel, cancelLabel, danger,
     input:{label,value,placeholder}, content:<DOM node placed above the buttons>, collect:fn}) -> Promise.
     Resolves the chosen value, true (OK; the input text when there is an input; collect()'s return value when
     given) or null (Cancel / Esc / backdrop). window.pmPrompt({title, label, value}) -> string|null. Both call pmDialog.
   - Declarative: every hx-confirm="..." (htmx:confirm) and every form/button with only data-confirm="..." gets
     the modal with no template change. A button with data-dlg-open="<id>" opens the static
     <dialog id="<id>" class="pmdlg"> (templates/_popup.html). After a non-GET htmx request from inside it: an answer
     swapped into the dialog itself (an error in its flash slot, a re-rendered panel) keeps it open; an answer for the
     page (#flash) closes it, so a success closes and an error is readable instead of dimmed under the backdrop.
   - Typed confirm: a static <dialog data-dlg-typed> holds [data-typed-label], [data-typed-expect], input[data-typed-in],
     an optional [data-typed-force] checkbox row and button[data-typed-go]. Its opener (data-dlg-open; the tmux list's Kill
     button, also marked data-kill-open) carries data-label / data-expect / data-attached and sits in the real form; Go (or
     Enter) fills the form's confirm field and force_attached and submits it once the text matches.
   Native <dialog>.showModal(): focus trap, Esc, top layer; works in the ?embed=1 drawer iframe. External file (strict CSP). */
(function () {
  'use strict';
  var d = document, seq = 0, downOut = null;
  var FIELD = 'input:not([type=hidden]):not([type=checkbox]):not([type=radio]):not([disabled]), select:not([disabled]), textarea:not([disabled])';
  var SIZES = { sm: 1, wide: 1, full: 1 };
  function el(tag, cls, text) { var n = d.createElement(tag); if (cls) n.className = cls; if (text != null) n.textContent = text; return n; }
  function q(root, sel) { return root.querySelector(sel); }
  function shown(n) { return !!(n && (n.offsetWidth || n.offsetHeight || n.getClientRects().length)); }
  function append(parent, x) { [].concat(x).forEach(function (n) { if (n) parent.appendChild(n); }); }

  function closeX() {  // the one close x (same icon as templates/_popup.html close_x)
    var b = el('button', 'pmdlg-x'); b.type = 'button'; b.setAttribute('data-dlg-close', '');
    b.setAttribute('aria-label', 'Close'); b.title = 'Close (Esc)';
    b.innerHTML = '<svg viewBox="0 0 24 24" width="16" height="16" aria-hidden="true" focusable="false" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round"><path d="M6 6l12 12M18 6L6 18"/></svg>';
    return b;
  }
  function build(o) {
    var dlg = el('dialog', 'pmdlg' + (SIZES[o.size] ? ' pmdlg-' + o.size : '') + (o.iframe ? ' pmdlg-frame' : '') + (o.cls ? ' ' + o.cls : ''));
    dlg.setAttribute('data-pm-dyn', '');
    if (o.label) dlg.setAttribute('aria-label', o.label);
    if (o.title || o.closeX) {
      var head = el('div', 'pmdlg-head');
      head.appendChild(el('p', 'pmdlg-title', o.title || ''));
      if (o.closeX) head.appendChild(closeX());
      dlg.appendChild(head);
    }
    if (o.body != null) dlg.appendChild(typeof o.body === 'string' ? el('p', 'small muted', o.body) : o.body);
    if (o.content) append(dlg, o.content);
    if (o.iframe) { var f = el('iframe', 'pmdlg-if'); f.title = o.label || o.title || ''; f.src = o.iframe; dlg.appendChild(f); }
    if (o.foot) { var ft = el('div', 'pmdlg-foot'); append(ft, o.foot); dlg.appendChild(ft); }
    d.body.appendChild(dlg);
    return dlg;
  }
  function wire(dlg) {
    if (dlg.__pmDlg) return;
    dlg.__pmDlg = true; dlg.setAttribute('data-pmdlg', '');
    dlg.addEventListener('close', function () {
      var cb = dlg.__pmOnClose; dlg.__pmOnClose = null;
      if (cb) cb(dlg.returnValue, dlg);
      if (dlg.hasAttribute('data-pm-dyn') && !dlg.__pmKeep && dlg.parentNode) dlg.parentNode.removeChild(dlg);
    });
  }
  function label(dlg) {  // unique title id, unless the dialog is already labelled
    var t = q(dlg, '.pmdlg-title'), by = dlg.getAttribute('aria-labelledby');
    if (!t || dlg.hasAttribute('aria-label') || (by && d.getElementById(by))) return;
    if (!t.id) t.id = 'pmdlg-t' + (++seq);
    dlg.setAttribute('aria-labelledby', t.id);
  }
  function focusIn(dlg, f) {
    var c = f || q(dlg, '[autofocus]');
    if (!shown(c)) c = Array.prototype.filter.call(dlg.querySelectorAll(FIELD), shown)[0];
    if (!c) c = q(dlg, '.pmdlg-foot [data-dlg-close]') || q(dlg, '[data-dlg-close]') || q(dlg, 'button');
    if (c && c.focus) c.focus();
  }
  function pmDialog(x, extra) {
    var given = !!(x && (x.nodeType || typeof x === 'string')), o = given ? (extra || {}) : (x || {});
    var dlg = !given ? build(o) : x.nodeType ? x : d.getElementById(x);
    if (!dlg) return null;
    wire(dlg); label(dlg);
    dlg.__pmKeep = !!o.keep; dlg.__pmOnClose = o.onClose || null; dlg.returnValue = '';
    if (!dlg.open) { if (dlg.showModal) dlg.showModal(); else dlg.setAttribute('open', ''); }
    focusIn(dlg, o.focus);
    return dlg;
  }
  pmDialog.close = function (dlg, v) { if (dlg && dlg.open) dlg.close(v == null ? '' : String(v)); };

  function pmConfirm(o) {
    o = o || {};
    return new Promise(function (resolve) {  // mounts synchronously: callers (continuepop.js) touch the content right after
      var result = null, input = null, foot = [], content = [], dlg;
      function finish(v) { result = v; pmDialog.close(dlg); }
      if (o.input) {
        var lab = el('label', 'small pmdlg-in', o.input.label || ''); input = el('input');
        input.type = 'text'; input.value = o.input.value || ''; input.placeholder = o.input.placeholder || '';
        input.autocomplete = 'off'; input.setAttribute('aria-label', o.input.label || o.title || 'Value');
        input.addEventListener('keydown', function (e) { if (e.key === 'Enter') { e.preventDefault(); finish(input.value); } });
        lab.appendChild(input); content.push(lab);
      }
      if (o.choices && o.choices.length) {
        var opts = el('div', 'pmdlg-opts');
        o.choices.forEach(function (c) {
          var row = el('div', 'pmdlg-choice'), b = el('button', 'small' + (c.danger ? ' danger' : ''), c.label);
          b.type = 'button'; b.setAttribute('data-dlg-choice', c.value);
          b.addEventListener('click', function () { finish(c.value); });
          row.appendChild(b); if (c.hint) row.appendChild(el('span', 'small muted', c.hint)); opts.appendChild(row);
        });
        content.push(opts);
      }
      if (o.content) content.push(o.content);
      var cancel = el('button', 'small', o.cancelLabel || 'Cancel'); cancel.type = 'button'; cancel.setAttribute('data-dlg-close', '');
      foot.push(cancel);
      if (!(o.choices && o.choices.length)) {
        var ok = el('button', o.danger ? 'small danger' : 'small primary', o.confirmLabel || 'OK'); ok.type = 'button'; ok.setAttribute('data-dlg-ok', '');
        ok.addEventListener('click', function () { finish(o.collect ? o.collect() : input ? input.value : true); });
        foot.push(ok);
      }
      dlg = build({ title: o.title || 'Are you sure?', body: o.body || null, content: content, foot: foot });
      dlg.setAttribute('data-pm-confirm', '');
      pmDialog(dlg, { focus: input || cancel, onClose: function () { resolve(result); } });  // the safe choice has focus
    });
  }
  function pmPrompt(o) {
    o = o || {};
    return pmConfirm({ title: o.title, body: o.body, input: { label: o.label, value: o.value, placeholder: o.placeholder }, confirmLabel: o.confirmLabel }).then(function (v) { return typeof v === 'string' ? v : null; });
  }
  window.pmDialog = pmDialog; window.pmConfirm = pmConfirm; window.pmPrompt = pmPrompt;

  function labelOf(elt) {
    var b = elt && elt.matches && elt.matches('button,input[type=submit]') ? elt : elt && elt.querySelector && elt.querySelector('button[type=submit],button:not([type])');
    var s = b ? (b.textContent || b.value || '').trim() : '';
    return { label: s && s.length <= 24 ? s : 'OK', danger: !!(b && b.classList.contains('danger')) };
  }

  // hx-confirm="..." -> modal instead of the native popup
  d.addEventListener('htmx:confirm', function (e) {
    var ask = e.detail && e.detail.question;
    if (!ask) return;
    e.preventDefault();
    var l = labelOf(e.detail.elt);
    pmConfirm({ title: ask, confirmLabel: l.label, danger: l.danger }).then(function (ok) { if (ok) e.detail.issueRequest(true); });
  });

  // data-confirm="..." on a plain form or button (no hx-confirm next to it)
  var passed = new WeakSet();
  d.addEventListener('submit', function (e) {
    var f = e.target, ask = f && f.getAttribute && f.getAttribute('data-confirm');
    if (!ask || f.hasAttribute('hx-confirm') || passed.has(f)) { if (f) passed.delete(f); return; }
    e.preventDefault(); e.stopImmediatePropagation();
    var l = labelOf(f);
    pmConfirm({ title: ask, confirmLabel: l.label, danger: l.danger }).then(function (ok) {
      if (!ok) return;
      passed.add(f); if (f.requestSubmit) f.requestSubmit(); else f.submit();
    });
  }, true);
  d.addEventListener('click', function (e) {
    var b = e.target && e.target.closest && e.target.closest('button[data-confirm],a[data-confirm]');
    if (!b || b.closest('form[data-confirm]') || b.hasAttribute('hx-confirm') || b.__pmOk) { if (b) b.__pmOk = false; return; }
    e.preventDefault(); e.stopImmediatePropagation();
    pmConfirm({ title: b.getAttribute('data-confirm'), confirmLabel: labelOf(b).label, danger: b.classList.contains('danger') }).then(function (ok) { if (ok) { b.__pmOk = true; b.click(); } });
  }, true);

  // typed confirm for kill: fill the dialog from its opener, enable Go on a match, then submit the opener's form
  function typedPrep(dl, o) {
    var exp = o.getAttribute('data-expect') || '', go = q(dl, '[data-typed-go]'), force = q(dl, '[data-typed-force]');
    var lab = q(dl, '[data-typed-label]'), ex = q(dl, '[data-typed-expect]'), inp = q(dl, '[data-typed-in]');
    dl.__pmForm = o.closest('form'); dl.__pmExpect = exp;
    if (lab) lab.textContent = o.getAttribute('data-label') || '';
    if (ex) ex.textContent = exp;
    if (inp) inp.value = '';
    if (go) go.disabled = true;
    if (force) { force.hidden = !o.hasAttribute('data-attached'); var cb = q(force, 'input'); if (cb) cb.checked = false; }
  }
  function typedGo(dl) {
    var f = dl.__pmForm, inp = q(dl, '[data-typed-in]'), v = inp ? inp.value : '';
    if (!f || !dl.__pmExpect || v !== dl.__pmExpect) return;
    var c = q(f, 'input[name=confirm]'), fa = q(f, 'input[name=force_attached]'), cb = q(dl, '[data-typed-force] input');
    if (c) c.value = v;
    if (fa) fa.disabled = !(cb && cb.checked);
    dl.__pmForm = null; pmDialog.close(dl);
    if (f.requestSubmit) f.requestSubmit(); else f.submit();
  }
  function typedIn(t) { return t && t.matches && t.matches('[data-typed-in]') ? t.closest('dialog[data-dlg-typed]') : null; }
  d.addEventListener('input', function (e) {
    var dl = typedIn(e.target), go = dl && q(dl, '[data-typed-go]');
    if (go) go.disabled = !(dl.__pmExpect && e.target.value === dl.__pmExpect);
  });
  d.addEventListener('keydown', function (e) {
    var dl = e.key === 'Enter' && typedIn(e.target);
    if (dl) { e.preventDefault(); typedGo(dl); }
  });

  // one backdrop rule for every pm dialog: a click that starts and ends outside the box (on the ::backdrop) closes it
  function outside(e, dlg) {
    var r = dlg.getBoundingClientRect();
    return e.clientX < r.left || e.clientX > r.right || e.clientY < r.top || e.clientY > r.bottom;
  }
  function pmDlg(n) { return !!n && n.tagName === 'DIALOG' && (n.classList.contains('pmdlg') || n.hasAttribute('data-pmdlg')); }
  d.addEventListener('pointerdown', function (e) { downOut = pmDlg(e.target) && e.target.open && outside(e, e.target) ? e.target : null; });

  // static dialogs: <button data-dlg-open="id"> ... <dialog id="id" class="pmdlg">; [data-dlg-close] and the backdrop close
  d.addEventListener('click', function (e) {
    var t = e.target;
    if (!t || !t.closest) return;
    var o = t.closest('[data-dlg-open]');
    if (o) {
      var dl = d.getElementById(o.getAttribute('data-dlg-open'));
      if (dl && dl.showModal && !dl.open) {
        e.preventDefault();
        if (dl.hasAttribute('data-dlg-typed')) typedPrep(dl, o);
        pmDialog(dl);
      }
      return;
    }
    var p = t.closest('dialog');
    if (!pmDlg(p) || !p.open) return;
    if (t.closest('[data-typed-go]')) typedGo(p);
    else if (t.closest('[data-dlg-close]')) pmDialog.close(p);
    else if (t === p && downOut === p && outside(e, p)) pmDialog.close(p);  // backdrop
  });
  d.addEventListener('htmx:afterRequest', function (e) {
    var x = e.detail || {}, dl = x.elt && x.elt.closest && x.elt.closest('dialog.pmdlg');
    if (!dl || !dl.open || dl.hasAttribute('data-pm-confirm')) return;
    if (((x.requestConfig && x.requestConfig.verb) || 'get').toLowerCase() === 'get') return;  // loading content into it
    if (x.target && dl.contains(x.target)) return;  // the answer (ok or error) is shown inside the dialog: keep it open
    pmDialog.close(dl);
  });
})();
