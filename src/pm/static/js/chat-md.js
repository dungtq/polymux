// data-confirm / hx-confirm forms are confirmed in-page by dialog.js (no native popups).

/* Markdown side panel. External file only (strict CSP); loaded after chat.js. */
(function () {
  'use strict';
  var REF = /(?:~\/|\.{0,2}\/)?[A-Za-z0-9_.\-\/]*[A-Za-z0-9_\-]\.md(?![A-Za-z0-9_])/g;
  var current = '';

  function split() { return document.getElementById('chatsplit'); }
  function pane() { return document.getElementById('mdpane'); }

  function linkify(root) {
    if (!root) return;
    var w = document.createTreeWalker(root, NodeFilter.SHOW_TEXT, {
      acceptNode: function (n) {
        if (!n.nodeValue || n.nodeValue.indexOf('.md') < 0) return NodeFilter.FILTER_REJECT;
        var p = n.parentNode;
        if (p && p.closest && p.closest('a, pre.pane, button')) return NodeFilter.FILTER_REJECT;
        return NodeFilter.FILTER_ACCEPT;
      }
    });
    var nodes = [], n;
    while ((n = w.nextNode())) nodes.push(n);
    nodes.forEach(function (node) {
      var text = node.nodeValue, last = 0, m, frag = null;
      REF.lastIndex = 0;
      while ((m = REF.exec(text))) {
        if (text.slice(Math.max(0, m.index - 3), m.index) === '://' || /[A-Za-z0-9]$/.test(text.slice(0, m.index)) && m[0].charAt(0) !== '/') continue;
        frag = frag || document.createDocumentFragment();
        frag.appendChild(document.createTextNode(text.slice(last, m.index)));
        var a = document.createElement('a');
        a.className = 'md-ref';
        a.href = '#';
        a.setAttribute('data-md', m[0]);
        a.title = 'View ' + m[0];
        a.textContent = m[0];
        frag.appendChild(a);
        last = m.index + m[0].length;
      }
      if (frag) {
        frag.appendChild(document.createTextNode(text.slice(last)));
        node.parentNode.replaceChild(frag, node);
      }
    });
  }

  function scan() {
    document.querySelectorAll('#chat .chat-text, #chat .chat-ask-text, #ci .chat-text, #ci-pending .chat-text').forEach(linkify);
  }

  function close() {
    var p = pane(), s = split();
    full(false);
    if (p) p.hidden = true;
    if (s) s.classList.remove('has-md');
  }

  function full(on) {
    var p = pane();
    if (!p) return;
    p.classList.toggle('is-full', !!on);
    document.documentElement.classList.toggle('md-full', !!on);
    var b = p.querySelector('[data-md-full]');
    if (b) {
      b.setAttribute('aria-pressed', on ? 'true' : 'false');
      b.textContent = on ? 'Exit full screen' : 'Full screen';
      b.setAttribute('aria-label', b.textContent);
    }
  }

  function dirOf(path) { var i = path.lastIndexOf('/'); return i < 0 ? '' : path.slice(0, i + 1); }

  function open(path, y) {
    var s = split(), p = pane();
    if (!s || !p) return;
    // framed in the drawer: the drawer (parent page) shows the file in a popup beside it, at the clicked message
    if (document.body.classList.contains('embed') && window.parent !== window) {
      try { window.parent.postMessage({ pm: 'drawer', act: 'md', id: s.getAttribute('data-agent'), path: path, y: y == null ? 120 : y }, location.origin); return; } catch (e) { /* fall through */ }
    }
    var id = s.getAttribute('data-agent');
    var body = p.querySelector('.mdpane-b'), title = p.querySelector('.mdpane-t');
    title.textContent = path;
    body.textContent = 'Loading…';
    p.hidden = false;
    s.classList.add('has-md');
    var wide = window.matchMedia && window.matchMedia('(min-width: 900px)').matches;
    // wide: the pane is sticky at the top of the viewport area (chat.css), not at the clicked message
    if (!wide) full(true); // narrow screen: popup over the page, Close returns to the chat
    window.pm.fetch('/a/' + encodeURIComponent(id) + '/md?path=' + encodeURIComponent(path), { headers: { Accept: 'text/html' } })
      .then(function (r) { return r.ok ? r.text() : Promise.reject(r.status); })
      .then(function (html) {
        body.innerHTML = html; // server-rendered, escaped markdown (markdown_render.py)
        var v = body.querySelector('.mdv');
        current = v ? v.getAttribute('data-path') : path;
        title.textContent = current;
        body.scrollTop = 0;
      })
      .catch(function (c) {
        body.textContent = c === 404
          ? 'Not found: ' + path + '. pm looked in the agent\'s folder, its project, the files its chat named and the ' +
            'folders it worked in. Files in hidden folders never open.'
          : 'Could not load file.';
      });
  }

  document.addEventListener('click', function (e) {
    var t = e.target;
    if (!t || !t.closest) return;
    var ref = t.closest('a[data-md]');
    if (ref) { e.preventDefault(); open(ref.getAttribute('data-md'), e.clientY); return; }
    if (t.closest('[data-md-close]')) { close(); return; }
    if (t.closest('[data-md-full]')) { var pp = pane(); full(!(pp && pp.classList.contains('is-full'))); return; }
    var a = t.closest('.mdpane-b a[href]');
    if (a) {
      var h = a.getAttribute('href') || '';
      var base = h.split('#')[0].split('?')[0];
      if (/\.md$/i.test(base) && !/^[a-z][a-z0-9+.\-]*:/i.test(base) && base.charAt(0) !== '/') {
        e.preventDefault();
        open(dirOf(current) + base);
      } else if (h.charAt(0) === '#') {
        e.preventDefault();
      }
    }
  });

  document.addEventListener('keydown', function (e) {
    var p = pane();
    if (e.key === 'Escape' && p && p.classList.contains('is-full')) { e.preventDefault(); full(false); }
  });

  // Normal chat order (items, then the live strip at the bottom): chatlog.js keeps the page at the bottom.
  document.addEventListener('htmx:afterSettle', function () { scan(); });
  document.addEventListener('htmx:load', function () { scan(); });
  document.addEventListener('pm:ci-update', function () { scan(); });  // chat-items.js inserted items
  if (document.readyState === 'loading') document.addEventListener('DOMContentLoaded', scan); else scan();
})();
