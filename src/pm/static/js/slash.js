/* Slash-command palette for every composer (catalog: the root's data-slash, JSON [{cmd, args, risk, desc}]; output card: its
   [data-cmp-slash-card]). Agent pages: the agent's commands; Launch: /plan, /advisor of the chosen kind (launch.js keeps
   data-slash in step with the Agent select); quick chat: its ticked skills. data-slash-at="word" (quick chat): the
   "/name" right before the caret anywhere in the text, not only a leading command. External file only (strict CSP). */
(function () {
  'use strict';

  function firstToken(text) {
    var m = /^\S*/.exec(String(text || ''));
    return m ? m[0] : '';
  }

  // Prefix matches on cmd first, then substring on cmd/desc. query includes the leading "/".
  function filter(items, query) {
    var q = String(query || '').toLowerCase();
    var bare = q.replace(/^\//, '');
    var pre = [], sub = [];
    (items || []).forEach(function (it) {
      var cmd = String(it.cmd || '').toLowerCase();
      var desc = String(it.desc || '').toLowerCase();
      if (cmd.indexOf(q) === 0) pre.push(it);
      else if (cmd.indexOf(bare) >= 0 || desc.indexOf(bare) >= 0) sub.push(it);
    });
    return pre.concat(sub);
  }

  // Returns the catalog entry when text is a danger command, else null.
  function needsConfirm(items, text) {
    var t = firstToken(text).toLowerCase();
    if (t.charAt(0) !== '/' || t.indexOf('/', 1) >= 0) return null;
    for (var i = 0; i < (items || []).length; i++) {
      var it = items[i];
      if (String(it.cmd || '').toLowerCase() === t && it.risk === 'danger') return it;
    }
    return null;
  }

  window.pmSlash = { filter: filter, firstToken: firstToken, needsConfirm: needsConfirm };

  if (typeof document === 'undefined') return;

  var list = null, shown = [], active = 0, ta = null;

  function rootOf(t) { return t && t.closest ? t.closest('[data-composer]') : null; }
  function word() { var r = rootOf(ta); return !!r && r.getAttribute('data-slash-at') === 'word'; }
  function wordTok() {  // the "/name" that ends right at the caret
    var m = /(^|\s)(\/[\w-]*)$/.exec(ta.value.slice(0, ta.selectionEnd == null ? ta.value.length : ta.selectionEnd));
    return m ? m[2] : '';
  }
  function catalog() {  // the composer's data-slash (none on an ended composer: no list there)
    var root = rootOf(ta);
    try {
      var v = JSON.parse((root && root.getAttribute('data-slash')) || '[]');
      return Array.isArray(v) ? v : [];
    } catch (e) { return []; }
  }
  function running() {  // the composer's own agent state (composer.js), else the page's state badge
    var root = rootOf(ta);
    if (root && window.pm && window.pm.composer && root.__cmp) return window.pm.composer.running(root);
    return !!document.querySelector('#agent-state .badge.st-running');
  }
  function blocked(it) { return it.risk === 'action' && running(); }
  function isComposer(t) { return !!(t && t.matches && t.matches('[data-composer] [data-cmp-input]')); }

  function close() {
    if (list && list.parentNode) list.parentNode.removeChild(list);
    list = null; shown = []; active = 0;
    if (ta) { ta.setAttribute('aria-expanded', 'false'); ta.removeAttribute('aria-activedescendant'); }
  }
  function isOpen() { return !!list; }

  function paint() {
    Array.prototype.forEach.call(list.children, function (li, i) {
      var on = i === active;
      li.setAttribute('aria-selected', on ? 'true' : 'false');
      li.classList.toggle('is-active', on);
      if (on) {
        ta.setAttribute('aria-activedescendant', li.id);
        if (li.scrollIntoView) li.scrollIntoView({ block: 'nearest' });
      }
    });
  }

  function mk(cls, text) {
    var s = document.createElement('span');
    s.className = cls;
    s.textContent = text || '';
    return s;
  }

  function insert(it) {
    if (!it || blocked(it)) return;
    var pos;
    if (word()) {  // replace the "/name" before the caret, keep the rest
      var end = ta.selectionEnd == null ? ta.value.length : ta.selectionEnd, head = ta.value.slice(0, end).replace(/\/[\w-]*$/, it.cmd + ' ');
      ta.value = head + ta.value.slice(end);
      pos = head.length;
    } else {
      var rest = ta.value.replace(/^\S*/, '').replace(/^\s+/, '');
      ta.value = it.cmd + ' ' + rest;
      pos = it.cmd.length + 1;
    }
    close();
    ta.focus();
    try { ta.setSelectionRange(pos, pos); } catch (e) {}
    ta.dispatchEvent(new Event('input', { bubbles: true }));
  }

  function open(items) {
    var host = ta.closest('.cbox') || ta.form;  // above the box (Launch: below it, launch.css)
    if (!host) return;
    if (!list) {
      list = document.createElement('ul');
      list.id = 'slash-list';
      list.className = 'slash-list';
      list.setAttribute('role', 'listbox');
      list.setAttribute('aria-label', 'Slash commands');
      host.appendChild(list);
      list.addEventListener('pointerdown', function (e) {
        var li = e.target.closest && e.target.closest('[role=option]');
        e.preventDefault();
        if (li) insert(shown[+li.getAttribute('data-i')]);
      });
      list.addEventListener('mousedown', function (e) { e.preventDefault(); });
    }
    shown = items;
    if (active >= shown.length) active = 0;
    while (list.firstChild) list.removeChild(list.firstChild);
    shown.forEach(function (it, i) {
      var li = document.createElement('li');
      li.id = 'slash-opt-' + i;
      li.className = 'slash-opt risk-' + (it.risk || 'info');
      li.setAttribute('role', 'option');
      li.setAttribute('data-i', String(i));
      li.setAttribute('aria-selected', 'false');
      if (blocked(it)) li.setAttribute('aria-disabled', 'true');
      li.appendChild(mk('slash-cmd', it.cmd));
      li.appendChild(mk('slash-args', it.args || ''));
      li.appendChild(mk('slash-risk', it.risk === 'danger' ? 'danger' : it.risk === 'action' ? 'action' : ''));
      li.appendChild(mk('slash-desc', it.desc || ''));
      list.appendChild(li);
    });
    ta.setAttribute('aria-controls', 'slash-list');
    ta.setAttribute('aria-expanded', 'true');
    paint();
  }

  function refresh() {
    var v = ta.value, w = word();
    var tok = w ? wordTok() : firstToken(v);
    if (w ? !tok : (v.charAt(0) !== '/' || tok.length !== v.search(/\s|$/) || /\s/.test(v.slice(tok.length, tok.length + 1)))) {
      return close();
    }
    var items = filter(catalog(), tok);
    if (!items.length) return close();
    open(items);
  }

  document.addEventListener('input', function (e) {
    if (!isComposer(e.target)) return;
    ta = e.target;
    refresh();
  });

  // Capture phase: runs before composer.js's Enter-to-send handler.
  document.addEventListener('keydown', function (e) {
    if (!isOpen() || !isComposer(e.target) || e.isComposing) return;
    var k = e.key;
    if (k === 'ArrowDown' || k === 'ArrowUp') {
      e.preventDefault();
      active = (active + (k === 'ArrowDown' ? 1 : -1) + shown.length) % shown.length;
      paint();
    } else if (k === 'Escape') {
      e.preventDefault();
      close();
    } else if ((k === 'Enter' && !e.shiftKey) || k === 'Tab') {
      var it = shown[active];
      if (!it) return;
      e.preventDefault();
      e.stopPropagation();
      insert(it);
    }
  }, true);

  document.addEventListener('focusout', function (e) {
    if (isComposer(e.target)) close();
  });

  document.addEventListener('click', function (e) {
    var b = e.target.closest && e.target.closest('[data-slash-dismiss]');
    if (!b) return;
    var c = b.closest('[data-cmp-slash-card]');
    if (c) while (c.firstChild) c.removeChild(c.firstChild);
  });

  document.addEventListener('htmx:configRequest', function (e) {
    var d = e.detail, form = d && d.elt;
    if (!form || !form.matches || !form.matches('[data-composer] [data-cmp-form]')) return;
    var t = form.querySelector('[data-cmp-input]');
    ta = t || ta;
    var text = (d.parameters && d.parameters.text) || (t ? t.value : '');
    var it = needsConfirm(catalog(), text);
    if (!it) return;
    if (form.getAttribute('data-slash-ok') !== '1') {  // ask in-page, then send the form again
      e.preventDefault();
      window.pmConfirm({ title: 'Send ' + it.cmd + '?', body: it.desc || '', confirmLabel: 'Send', danger: true }).then(function (ok) {
        if (!ok) return;
        form.setAttribute('data-slash-ok', '1');
        if (window.htmx) window.htmx.trigger(form, 'submit');
      });
      return;
    }
    form.removeAttribute('data-slash-ok');
    d.parameters.confirm = '1';
  });
})();
