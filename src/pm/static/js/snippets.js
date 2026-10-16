/* Composer snippets: the Send caret opens a small menu (pm.popover, popup.js: placement, Esc, outside click, arrow keys;
 * a bottom sheet on phones) that inserts canned text at the cursor of its own composer. Never sends.
 * The last item, "Save as issue", belongs to issue-save.js. */
(function () {
  'use strict';
  var SNIPPETS = [
    ['Check backlog', 'Check BACKLOG.md for open items.'],
    ['Check TODO', 'Check TODO.md for the next open task.'],
    ['Go ahead', 'Go ahead.'],
    ['Skills, rules, sub-agents', 'Remember to use the project skills and rules, and split the work across sub-agents where it helps.'],
    ['Run tests', 'Run the tests and fix any failures.'],
    ['Continue', 'Continue where you left off.']
  ];
  function build(m) {
    if (m.childElementCount) return;
    SNIPPETS.forEach(function (s) {
      var b = document.createElement('button');
      b.type = 'button'; b.className = 'snipitem pop-item'; b.setAttribute('role', 'menuitem');
      b.setAttribute('data-snip-text', s[1]); b.title = s[1]; b.textContent = s[0];
      m.appendChild(b);
    });
    // "Save as issue" (issue-save.js handles the click); only where that script is loaded (live composer)
    if (!(window.pm && window.pm.issueSave)) return;
    var sep = document.createElement('div');
    sep.className = 'snipsep'; sep.setAttribute('role', 'separator');
    var it = document.createElement('button');
    it.type = 'button'; it.className = 'snipitem pop-item'; it.setAttribute('role', 'menuitem'); it.setAttribute('data-issue-save', '');
    it.title = 'Save the text and images as an issue in this project (not sent)'; it.textContent = 'Save as issue';
    m.appendChild(sep); m.appendChild(it);
  }
  function insert(ta, text) {
    var v = ta.value, a = ta.selectionStart == null ? v.length : ta.selectionStart, e = ta.selectionEnd == null ? a : ta.selectionEnd;
    var pre = v.slice(0, a), post = v.slice(e);
    if (pre && !/\s$/.test(pre)) pre += ' ';
    if (post && !/^\s/.test(post)) text += ' ';
    ta.value = pre + text + post;
    var pos = (pre + text).length;
    ta.focus();
    try { ta.setSelectionRange(pos, pos); } catch (err) { /* ignore */ }
    ta.dispatchEvent(new Event('input', { bubbles: true }));
  }
  window.pmSnippets = { list: SNIPPETS, insert: insert };
  document.addEventListener('click', function (ev) {
    var t = ev.target, pop = window.pm && window.pm.popover;
    if (!t || !t.closest || !pop) return;
    var tog = t.closest('[data-snip]');
    if (tog) {
      var m = tog.parentNode.querySelector('.snipmenu');
      if (!m) return;
      build(m);
      pop.toggle(tog, m, { placement: 'top-end', role: 'menu' });
      return;
    }
    var it = t.closest('[data-snip-text]');
    if (!it) return;
    var root = it.closest('[data-composer]'), ta = root && root.querySelector('[data-cmp-input]');
    pop.close(it.closest('.snipmenu'));
    if (ta) insert(ta, it.getAttribute('data-snip-text'));
  });
})();
