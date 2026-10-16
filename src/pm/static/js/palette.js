/* Command palette: Cmd/Ctrl+Shift+P opens a filter box over a list of commands (launch, stop, chat, go to, theme...).
   Arrows move, Enter runs, Esc closes. Commands never POST themselves: they click the existing button / link / form
   (so CSRF, hx-confirm and the PANIC dialog stay intact) or navigate. A command whose trigger is not on the page is hidden.
   Cmd/Ctrl+K, Cmd/Ctrl+P and "/" stay with the navbar search (search.js). External file (strict CSP). */
(function () {
  'use strict';
  var dlg = document.getElementById('palette');
  if (!dlg || !window.pmDialog) return;
  var input = dlg.querySelector('.pal-in'), list = dlg.querySelector('.pal-list'), empty = dlg.querySelector('.pal-empty');
  var idx = 0, shown = [];
  function q(sel) { return document.querySelector(sel); }
  function click(sel) { return function () { var b = q(sel); if (b) b.click(); }; }
  function go(href) { return function () { window.location.href = href; }; }
  function has(sel) { return function () { return !!q(sel); }; }

  var CMDS = [
    { g: 'Do', l: 'Launch an agent', k: 'new start run', run: click('.tb-launch'), when: has('.tb-launch') },
    { g: 'Do', l: 'Stop all agents', k: 'panic halt ctrl-c', run: click('.tb-stop'), when: has('.tb-stop') },
    { g: 'Do', l: 'Quick chat', k: 'new chat ask claude', run: click('[data-qchat-open]'), when: has('[data-qchat-open]') },
    { g: 'Do', l: 'Search', k: 'find names chats files', run: function () {
      var b = q('#gsearch'), i = b && b.querySelector('.gs-in'); if (i) { b.classList.add('open'); i.focus(); i.select(); } }, when: has('#gsearch .gs-in') },
    { g: 'Do', l: 'Open settings', k: 'preferences config', run: function () {
      var s = q('#vs-settings-open'); if (s) s.click(); else window.location.href = '/settings'; } },
    { g: 'View', l: 'Toggle dark / light mode', k: 'theme colour color', run: click('[data-theme-toggle]'), when: has('[data-theme-toggle]') },
    { g: 'View', l: 'Larger chat text', k: 'zoom font size increase', run: click('[data-chat-zoom="1"]'), when: has('[data-chat-zoom="1"]') },
    { g: 'View', l: 'Smaller chat text', k: 'zoom font size decrease', run: click('[data-chat-zoom="-1"]'), when: has('[data-chat-zoom="-1"]') },
    { g: 'View', l: 'Reset chat text size', k: 'zoom font', run: click('[data-chat-zoom="0"]'), when: has('[data-chat-zoom="0"]') },
    { g: 'View', l: 'Reload this page', k: 'refresh', run: function () { window.location.reload(); } },
    { g: 'Go to', l: 'Inbox', k: 'home', run: go('/') },
    { g: 'Go to', l: 'Projects', k: 'repos', run: go('/p') },
    { g: 'Go to', l: 'tmux', k: 'sessions windows panes', run: go('/tmux') },
    { g: 'Go to', l: 'Agents', k: 'sessions', run: go('/agents') },
    { g: 'Go to', l: 'Servers', k: 'hosts', run: go('/servers') },
    { g: 'Go to', l: 'Backlog', k: 'todo tasks', run: go('/backlog') },
    { g: 'Go to', l: 'Audit', k: 'log history', run: go('/audit') },
    { g: 'Go to', l: 'Settings', k: 'preferences config', run: go('/settings') },
    { g: 'Service', l: 'Reload pm service', k: 'restart', run: click('form[action="/service/reload"] button'), when: has('form[action="/service/reload"] button') },
    { g: 'Service', l: 'Log out', k: 'sign out', run: click('form[action="/logout"] button'), when: has('form[action="/logout"] button') }
  ];

  function el(tag, cls, text) { var n = document.createElement(tag); if (cls) n.className = cls; if (text != null) n.textContent = text; return n; }
  function render() {
    var terms = input.value.toLowerCase().split(/\s+/).filter(Boolean);
    shown = CMDS.filter(function (c) {
      if (c.when && !c.when()) return false;
      var hay = (c.g + ' ' + c.l + ' ' + (c.k || '')).toLowerCase();
      return terms.every(function (t) { return hay.indexOf(t) >= 0; });
    });
    list.textContent = '';
    shown.forEach(function (c, i) {
      var li = el('li', 'pal-it'); li.id = 'pal-' + i; li.setAttribute('role', 'option');
      li.appendChild(el('span', 'pal-l', c.l)); li.appendChild(el('span', 'pal-g muted small', c.g));
      li.addEventListener('mousemove', function () { if (idx !== i) mark(i); });
      li.addEventListener('click', function () { run(i); });
      list.appendChild(li);
    });
    empty.hidden = shown.length > 0;
    mark(0);
  }
  function mark(i) {
    var n = list.children;
    idx = n.length ? (i + n.length) % n.length : 0;
    for (var j = 0; j < n.length; j++) { n[j].classList.toggle('on', j === idx); n[j].setAttribute('aria-selected', j === idx ? 'true' : 'false'); }
    if (n[idx]) { n[idx].scrollIntoView({ block: 'nearest' }); input.setAttribute('aria-activedescendant', n[idx].id); }
    else input.removeAttribute('aria-activedescendant');
  }
  function run(i) {
    var c = shown[i];
    if (!c) return;
    dlg.close();
    setTimeout(c.run, 0);  // after the modal is gone, so a command that opens another dialog works
  }
  function open() {
    input.value = '';
    render();
    window.pmDialog(dlg, { focus: input });  // dialog.js: backdrop, Esc
  }

  input.addEventListener('input', render);
  input.addEventListener('keydown', function (e) {
    if (e.key === 'ArrowDown') { e.preventDefault(); mark(idx + 1); }
    else if (e.key === 'ArrowUp') { e.preventDefault(); mark(idx - 1); }
    else if (e.key === 'Enter' && !e.isComposing) { e.preventDefault(); run(idx); }
  });

  window.addEventListener('message', function (e) {  // forwarded by an embedded page (keyfwd.js), same-origin child frames only
    var m = e.data;
    if (e.origin !== location.origin || !m || m.pm !== 'shortcut' || m.act !== 'palette') return;
    if (e.source === window || !Array.prototype.some.call(document.querySelectorAll('iframe'), function (f) { return f.contentWindow === e.source; })) return;
    if (dlg.open) { dlg.close(); return; }
    if (document.querySelector('dialog[open]')) return;
    window.focus(); open();
  });

  document.addEventListener('keydown', function (e) {
    if (!(e.metaKey || e.ctrlKey) || !e.shiftKey || e.altKey || e.isComposing) return;
    if ((e.key || '').toLowerCase() !== 'p') return;
    e.preventDefault(); e.stopImmediatePropagation();  // the browser's own binding; search.js must not also react
    if (dlg.open) { dlg.close(); return; }
    if (document.querySelector('dialog[open]')) return;
    open();
  }, true);
})();
