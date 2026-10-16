/* "@" file picker for every composer with a listing URL (its root's data-atfiles: /a/<id>/atfiles, Launch /p/<name>/atfiles).
   External file only (strict CSP). @foo -> entries of the agent's cwd (Launch: the project dir); @sub/ -> that dir; @/abs, @~/x, @../x -> another dir. */
(function () {
  'use strict';

  // The @token ending at pos: {start, text} or null. "@" must start the text or follow whitespace.
  function tokenAt(value, pos) {
    var before = String(value || '').slice(0, pos);
    var m = /(^|\s)@(\S*)$/.exec(before);
    return m ? { start: before.length - m[2].length - 1, text: m[2] } : null;
  }
  // Split "a/b/c" -> {dir:"a/b/", q:"c"}.
  function split(text) {
    var i = text.lastIndexOf('/');
    return i < 0 ? { dir: '', q: text } : { dir: text.slice(0, i + 1), q: text.slice(i + 1) };
  }
  window.pmAtFiles = { tokenAt: tokenAt, split: split };

  if (typeof document === 'undefined') return;

  var list = null, shown = [], active = 0, ta = null, tok = null, seq = 0, timer = 0;

  function isComposer(t) { return !!(t && t.matches && t.matches('[data-composer] [data-cmp-input]')); }
  // The listing URL: the composer's data-atfiles, read on every fetch (none on an ended composer or quick chat: no list there).
  function endpoint() {
    var root = ta && ta.closest('[data-composer]');
    return (root && root.getAttribute('data-atfiles')) || '';
  }
  function close() {
    if (list && list.parentNode) list.parentNode.removeChild(list);
    list = null; shown = []; active = 0; tok = null;
    if (ta) ta.removeAttribute('aria-activedescendant');
  }
  function paint() {
    Array.prototype.forEach.call(list.querySelectorAll('[role=option]'), function (li, i) {
      var on = i === active;
      li.setAttribute('aria-selected', on ? 'true' : 'false');
      li.classList.toggle('is-active', on);
      if (on) { ta.setAttribute('aria-activedescendant', li.id); if (li.scrollIntoView) li.scrollIntoView({ block: 'nearest' }); }
    });
  }
  function span(cls, text) {
    var s = document.createElement('span'); s.className = cls; s.textContent = text; return s;
  }
  function pick(it) {
    if (!it || !tok) return;
    var sp = split(tok.text);
    var path = sp.dir + it.name + (it.dir ? '/' : '');
    var v = ta.value, end = tok.start + 1 + tok.text.length;
    ta.value = v.slice(0, tok.start) + '@' + path + (it.dir ? '' : ' ') + v.slice(end);
    var pos = tok.start + 1 + path.length + (it.dir ? 0 : 1);
    ta.focus();
    try { ta.setSelectionRange(pos, pos); } catch (e) {}
    ta.dispatchEvent(new Event('input', { bubbles: true }));
  }
  function note(text) {
    var n = document.createElement('li');
    n.className = 'atfiles-head muted small';
    n.textContent = text;
    list.appendChild(n);
  }
  function render(data) {
    if (!list) {
      list = document.createElement('ul');
      list.id = 'atfiles-list';
      list.className = 'slash-list atfiles-list';
      list.setAttribute('role', 'listbox');
      list.setAttribute('aria-label', 'Files');
      (ta.closest('.cbox') || ta.form).appendChild(list);  // above the box (Launch: below it, launch.css)
      list.addEventListener('pointerdown', function (e) {
        var li = e.target.closest && e.target.closest('[role=option]');
        e.preventDefault();
        if (li) pick(shown[+li.getAttribute('data-i')]);
      });
      list.addEventListener('mousedown', function (e) { e.preventDefault(); });
    }
    shown = data.items || [];
    if (active >= shown.length) active = 0;
    while (list.firstChild) list.removeChild(list.firstChild);
    note(data.base === 'denied' ? 'Not allowed: ' + data.dir : data.base === 'other' ? 'Other dir: ' + data.dir
      : 'Project dir: ' + data.dir + '  (type / or ~/ for another dir)');
    shown.forEach(function (it, i) {
      var li = document.createElement('li');
      li.id = 'atfiles-opt-' + i;
      li.className = 'slash-opt';
      li.setAttribute('role', 'option');
      li.setAttribute('data-i', String(i));
      li.setAttribute('aria-selected', 'false');
      li.appendChild(span('slash-cmd', it.name + (it.dir ? '/' : '')));
      list.appendChild(li);
    });
    if (shown.length) paint(); else note('No matches');
  }
  function refresh() {
    tok = tokenAt(ta.value, ta.selectionStart);
    if (!tok) return close();
    var sp = split(tok.text), ep = endpoint(), my = ++seq;
    if (!ep) return close();
    var url = ep + '?dir=' + encodeURIComponent(sp.dir) + '&q=' + encodeURIComponent(sp.q);
    window.pm.fetch(url, { credentials: 'same-origin', headers: { Accept: 'application/json' } })
      .then(function (r) { return r.ok ? r.json() : null; })
      .then(function (d) { if (d && my === seq && tok) render(d); })
      .catch(function () {});
  }

  document.addEventListener('input', function (e) {
    if (!isComposer(e.target)) return;
    ta = e.target;
    clearTimeout(timer);
    timer = setTimeout(refresh, 80);
  });
  document.addEventListener('keydown', function (e) {
    if (!list || !isComposer(e.target) || e.isComposing) return;
    var k = e.key;
    if ((k === 'ArrowDown' || k === 'ArrowUp') && shown.length) {
      e.preventDefault(); e.stopPropagation();
      active = (active + (k === 'ArrowDown' ? 1 : -1) + shown.length) % shown.length; paint();
    } else if (k === 'Escape') {
      e.preventDefault(); e.stopPropagation(); close();
    } else if (((k === 'Enter' && !e.shiftKey) || k === 'Tab') && shown.length) {
      e.preventDefault(); e.stopPropagation(); pick(shown[active]);
    }
  }, true);
  document.addEventListener('focusout', function (e) { if (isComposer(e.target)) close(); });
})();
