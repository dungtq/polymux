/* v5 right-hand drawer. A plain click on an agent or terminal link inside <main> opens the same page with
   ?embed=1 in an iframe; ctrl/cmd/shift/alt and middle click fall through, so the row stays a real link. */
(function () {
  var d = document, drawer = d.getElementById('v5chat');
  if (!drawer) return;
  var frame = d.getElementById('vc-frame'), cur = [], curHref = '', curPane = '';
  var AGENT = /^\/a\/[a-z0-9]+(?:-[a-z0-9]+)*$/, TERM = /^\/term\/s\d+$/;
  /* plan .md links on the project page open here too, but only in the full (wide) layout and only while no chat aside is open (then they open in the page, the aside stays); narrow screens navigate */
  var PLAN = /^\/p\/[^/?#]+\/plans\/[^/?#]+(?:\?src=[a-z]+)?$/, WIDE = window.matchMedia('(min-width:900px)');
  function kindOf(a) {
    var u; try { u = new URL(a.getAttribute('href'), location.href); } catch (e) { return null; }
    if (u.origin !== location.origin) return null;
    if (AGENT.test(u.pathname) && !u.search) return 'agent';
    if (TERM.test(u.pathname)) return 'term';
    if (PLAN.test(u.pathname + u.search) && WIDE.matches && a.closest('.pg-plans') && !d.body.classList.contains('chat-open')) return 'plan';
    return null;
  }
  /* every row that lists the open agent (matched by its /a/ID link, not its label): agents list, ended sessions, inbox, tmux
     windows, ntfy rows (.ntfy-row), NOT history rows (plain --hover only). Rows are the link itself (a.lrow/a.nrow/a.hist-row) or the container holding it. */
  var ROW = '.lrow,.nrow,.tx-row,.pg-plans li,.ntfy-row';
  function mark(href) {
    d.querySelectorAll('.v5-active').forEach(function (x) { x.classList.remove('v5-active'); });
    cur = [];
    if (!href) return;
    var all = d.querySelectorAll('main a[href="' + href.replace(/"/g, '') + '"]'), i, row;
    for (i = 0; i < all.length; i++) {
      /* a hidden duplicate (collapsed section) is not marked; a bare link outside any row stays unmarked */
      if (!all[i].getClientRects().length) continue;
      row = all[i].matches(ROW) ? all[i] : all[i].closest(ROW);
      if (row && cur.indexOf(row) < 0) cur.push(row);
    }
    /* a session id rotates (/clear, New Chat, resume) but the tmux pane stays: no row for this id, use the live row of its pane */
    if (!cur.length && curPane && /^%\d+$/.test(curPane)) {
      row = d.querySelector('main a.arow[data-pane="' + curPane + '"], main a.nrow[data-pane="' + curPane + '"]');
      if (row) cur.push(row);
    }
    cur.forEach(function (r) { r.classList.add('v5-active'); });
  }
  /* a list refresh (htmx poll/swap, nodiff morph that rewrites class=, filter, sort) drops the class: put it back on the open one */
  var mm = d.querySelector('main'), pend = 0;
  function remark() {
    pend = 0;
    if (!curHref) return;
    if (cur.length && cur.every(function (r) { return r.isConnected && r.classList.contains('v5-active'); })) return;
    mark(curHref);
  }
  if (mm && window.MutationObserver) {
    new MutationObserver(function () { if (!pend) pend = requestAnimationFrame(remark); })
      .observe(mm, { childList: true, subtree: true, attributes: true, attributeFilter: ['class'] });
  }
  /* state lives in the URL hash (#chat=/a/ID or #term=sN) so reload, back/forward and shared links reopen it */
  function hashOf(href) {
    return AGENT.test(href) ? '#chat=' + href : TERM.test(href) ? '#term=' + href.split('/').pop()
      : PLAN.test(href) ? '#plan=' + encodeURIComponent(href) : '';
  }
  function fromHash() {
    var m = /^#(chat|term|plan)=(.+)$/.exec(location.hash), v;
    if (!m) return null;
    try { v = decodeURIComponent(m[2]); } catch (e) { return null; }
    var href = m[1] === 'term' ? '/term/' + v : v;
    return (m[1] === 'chat' ? AGENT : m[1] === 'plan' ? PLAN : TERM).test(href) ? href : null;
  }
  function setHash(h, push) {
    if ((location.hash || '') === h) return;
    try { history[push ? 'pushState' : 'replaceState'](null, '', location.pathname + location.search + h); } catch (e) { /* ignore */ }
  }
  /* location.replace keeps iframe navigations out of the joint session history, so Back steps the drawer hash */
  function load(url) {
    try { frame.contentWindow.location.replace(url); } catch (e) { frame.src = url; }
  }
  /* model pill next to the title (the title already carries the agent id): model (read from the same-origin embed page once loaded) */
  function pills(model) {
    var m = d.getElementById('vc-model');
    m.textContent = model; m.title = model ? 'Model ' + model : ''; m.hidden = !model;
  }
  /* Tabs in the header line (.tab look, tabs.css): mirror the framed page's Chat / Sub-agents / Pane / Terminal radios (same
     origin). A click checks the framed radio; pm.tabs marks the one whose radio is checked (change event, plus a slow poll for
     script-set tabs). Arrow keys: pm.tabs (click on the next tab). */
  var tabsEl = d.getElementById('vc-tabs'), zoomEl = d.getElementById('vc-zoom'), tabDoc = null, tabTimer = 0;
  function tabsClear() { tabsEl.replaceChildren(); tabsEl.hidden = true; tabDoc = null; if (tabTimer) { clearInterval(tabTimer); tabTimer = 0; } }
  function tabsSync() {
    if (!tabDoc) return;
    try {
      var on = null;
      tabsEl.querySelectorAll('.vc-tab').forEach(function (b) {
        var r = tabDoc.getElementById(b.getAttribute('data-for'));
        if (r && r.checked) on = b; else b.setAttribute('aria-selected', 'false');
      });
      if (on) window.pm.tabs.select(on, { silent: true });
    } catch (e) { tabsClear(); }
  }
  function tabsBuild(doc) {
    tabsClear();
    var labels = doc.querySelectorAll('.tabs label.tab[for]');
    if (!labels.length) return;
    labels.forEach(function (l) {
      var b = d.createElement('button');
      b.type = 'button'; b.className = 'tab vc-tab'; b.setAttribute('role', 'tab'); b.setAttribute('aria-selected', 'false'); b.setAttribute('data-for', l.getAttribute('for'));
      b.textContent = l.textContent.trim(); if (l.title) b.title = l.title;
      b.addEventListener('click', function () {
        try { var r = doc.getElementById(b.getAttribute('data-for')); if (r) r.click(); } catch (e) { /* frame gone */ }
        tabsSync();
      });
      tabsEl.appendChild(b);
    });
    tabsEl.hidden = false; tabDoc = doc;
    doc.addEventListener('change', tabsSync);
    tabsSync(); tabTimer = setInterval(tabsSync, 1000);
  }
  /* ended record: Resume / Fork live in the aside header; a click presses the framed page's own button (its form, CSRF and in-page confirm) */
  var actsEl = d.getElementById('vc-acts');
  function actsClear() { actsEl.replaceChildren(); actsEl.hidden = true; }
  function actsBuild(doc) {
    actsClear();
    var src = doc.querySelectorAll('.agent-head[data-mode="ended"] .agent-actions button');
    src.forEach(function (sb) {
      var b = d.createElement('button');
      b.type = 'button'; b.className = 'small vc-act'; b.textContent = sb.textContent.trim(); b.title = sb.title || '';
      if (sb.disabled) b.disabled = true;
      b.addEventListener('click', function () { try { sb.click(); } catch (e) { /* frame gone */ } });
      actsEl.appendChild(b);
    });
    actsEl.hidden = !src.length;
  }
  frame.addEventListener('load', function () {
    try { var fd = frame.contentDocument; if (fd && curHref && AGENT.test(curHref) && fd.location.pathname === curHref) tabsBuild(fd); else { tabsClear(); actsClear(); } } catch (e) { tabsClear(); actsClear(); }
    var id = curHref && AGENT.test(curHref) ? curHref.split('/').pop() : '';
    if (!id) return;
    try {
      var doc = frame.contentDocument, head = doc && doc.querySelector('.agent-head[data-agent]');
      if (!head || head.getAttribute('data-agent') !== id) return;
      var p = head.querySelector('.agent-title[data-model]'), model = p ? p.getAttribute('data-model').trim().replace(/^claude-/, '') : '';
      if (model && id.indexOf(model) !== 0) pills(model);
      /* a model switch in the framed composer (HX-Trigger pm:composer {pill}, web/agentctl.py): the aside pill follows */
      doc.addEventListener('pm:composer', function (ev) {
        var x = ev.detail || {}, mm = typeof x.pill === 'string' ? x.pill.trim().replace(/^claude-/, '') : '';
        if (mm && id.indexOf(mm) !== 0) pills(mm);
      });
      actsBuild(doc);
    } catch (e) { /* cross-origin or gone: no model pill */ }
  });
  function close(nohash) {
    if (typeof mdClose === "function") mdClose();
    d.body.classList.remove('chat-open'); curPane = ''; mark(''); curHref = ''; load('about:blank'); pills(''); tabsClear(); actsClear(); d.getElementById('vc-ico').setAttribute('hidden', '');
    if (nohash !== true) setHash('', true);
  }
  function open(a, kind, nohash) {
    var href = a.getAttribute('href'), term = kind === 'term', plan = kind === 'plan', row = a.closest('.lrow,.tx-row') || a;
    var strong = a.querySelector('strong') || row.querySelector('strong'), dot = term || plan ? null : row.querySelector('.dot');
    d.getElementById('vc-title').textContent = a.getAttribute('data-title') || (strong || a).textContent.trim();
    d.getElementById('vc-kind').textContent = plan ? 'plan' : '';
    var kel = term || plan ? null : (a.closest('[data-kind]') || row.querySelector('[data-kind]')), vh = d.querySelector('#v5chat .vc-head');
    if (vh) { if (kel) vh.setAttribute('data-kind', kel.getAttribute('data-kind')); else vh.removeAttribute('data-kind'); }
    d.getElementById('vc-ico').toggleAttribute('hidden', !term);
    var vd = d.getElementById('vc-dot'), m = dot && dot.className.match(/dot-\w+/);
    vd.style.display = term || plan ? 'none' : ''; vd.className = 'dot ' + (m ? m[0] : 'dot-idle');
    pills(''); tabsClear(); actsClear(); zoomEl.hidden = term || plan;
    d.getElementById('vc-full').href = href;
    d.getElementById('vc-full').title = 'Open the full ' + (term ? 'terminal' : plan ? 'plan' : 'agent') + ' page in a new browser tab';
    load(href + (href.indexOf('?') > -1 ? '&' : '?') + 'embed=1');
    curHref = href; curPane = row.getAttribute('data-pane') || a.getAttribute('data-pane') || ''; mark(href);
    d.body.classList.add('chat-open'); d.body.classList.remove('side-open');
    if (nohash !== true) setHash(hashOf(href), true);
  }
  /* open from a bare href (restore): reuse the row link when present, else a synthetic link with an id title */
  function openHref(href) {
    var a = d.querySelector('main a[href="' + href + '"]');
    if (!a) {
      a = d.createElement('a'); a.setAttribute('href', href);
      a.setAttribute('data-title', PLAN.test(href) ? decodeURIComponent(href.split('/').pop().split('?')[0]) + '.md' : href.split('/').pop());
    }
    open(a, TERM.test(href) ? 'term' : PLAN.test(href) ? 'plan' : 'agent', true);
  }
  function sync() {
    var h = fromHash();
    if (h) { if (h !== curHref) openHref(h); }
    else if (d.body.classList.contains('chat-open')) {
      /* an in-page anchor link (/#ended-sessions) replaced the hash: keep the aside, put its hash back, scroll to the anchor */
      var an = location.hash, t = null;
      if (an.length > 1 && curHref) {
        setHash(hashOf(curHref), false);
        try { t = d.getElementById(decodeURIComponent(an.slice(1))); } catch (e) { t = null; }
        if (t && t.scrollIntoView) t.scrollIntoView();
      } else close(true);
    }
  }
  /* the framed agent page reports its real id (New Chat = new session = new id): follow it without reloading the frame */
  /* markdown file popup in the drawer aside: opens near the top of the chat area (below the aside header) and ends above the
     framed page's composer (.composer--dock), so the message box and the context row stay visible; not tied to the clicked message */
  var mdp = d.getElementById('v5md'), mdCur = '', mdRO = null;
  function mdPlace() {
    if (!mdp || mdp.hidden) return;
    var fr = frame.getBoundingClientRect(), dr = drawer.getBoundingClientRect(), head = fr.top - dr.top, bar = null, cut = fr.height;
    try { bar = frame.contentDocument && frame.contentDocument.querySelector('.composer--dock'); } catch (e) { bar = null; }
    if (bar) { var r = bar.getBoundingClientRect(); if (r.height > 0) cut = Math.min(cut, Math.max(0, r.top)); }
    mdp.style.setProperty('--md-top', (head + 8) + 'px');
    mdp.style.setProperty('--md-bottom', Math.max(8, dr.height - (head + cut) + 8) + 'px');
  }
  function mdWatch() {
    if (mdRO) { mdRO.disconnect(); mdRO = null; }
    try {
      var bar = frame.contentDocument && frame.contentDocument.querySelector('.composer--dock');
      if (bar && frame.contentWindow.ResizeObserver) { mdRO = new frame.contentWindow.ResizeObserver(mdPlace); mdRO.observe(bar); }
    } catch (e) { mdRO = null; }
  }
  function mdClose() { if (!mdp) return; mdp.hidden = true; mdFull(false); if (mdRO) { mdRO.disconnect(); mdRO = null; } }
  function mdFull(on) {
    if (!mdp) return;
    mdp.classList.toggle('is-full', !!on);
    var b = d.getElementById('v5md-full'); b.setAttribute('aria-pressed', on ? 'true' : 'false'); b.textContent = on ? 'Exit full screen' : 'Full screen';
  }
  function mdOpen(id, path) {
    if (!mdp) return;
    var body = d.getElementById('v5md-b');
    d.getElementById('v5md-t').textContent = path;
    body.textContent = 'Loading…';
    mdp.hidden = false;
    mdPlace(); mdWatch();
    window.pm.fetch('/a/' + encodeURIComponent(id) + '/md?path=' + encodeURIComponent(path), { credentials: 'same-origin', headers: { Accept: 'text/html' } })
      .then(function (r) { return r.ok ? r.text() : Promise.reject(r.status); })
      .then(function (html) {
        body.replaceChildren(...new DOMParser().parseFromString(html, "text/html").body.childNodes); /* server-rendered, escaped markdown */
        var v = body.querySelector('.mdv');
        mdCur = v ? v.getAttribute('data-path') : path;
        mdp.dataset.agent = id;
        d.getElementById('v5md-t').textContent = mdCur;
        body.scrollTop = 0;
      })
      .catch(function (c) { body.textContent = c === 404 ? 'Not found: ' + path + '. Files in hidden folders never open.' : 'Could not load file.'; });
  }
  if (mdp) {
    d.getElementById('v5md-close').addEventListener('click', mdClose);
    d.getElementById('v5md-full').addEventListener('click', function () { mdFull(!mdp.classList.contains('is-full')); });
    mdp.addEventListener('click', function (e) {
      var a = e.target.closest && e.target.closest('.mdpane-b a[href]');
      if (!a) return;
      var h = a.getAttribute('href') || '', base = h.split('#')[0].split('?')[0];
      if (/\.md$/i.test(base) && !/^[a-z][a-z0-9+.\-]*:/i.test(base) && base.charAt(0) !== '/') {
        e.preventDefault();
        mdOpen(mdp.dataset.agent, mdCur.slice(0, mdCur.lastIndexOf('/') + 1) + base);
      } else if (h.charAt(0) === '#') e.preventDefault();
    });
    window.addEventListener('resize', mdPlace);
    frame.addEventListener('load', function () { mdPlace(); mdWatch(); });
  }
  window.addEventListener('message', function (e) {
    var m = e.data, href;
    if (e.origin === location.origin && e.source === frame.contentWindow && m && m.pm === 'drawer' && m.act === 'md' && /^[A-Za-z0-9_.\-]+$/.test(String(m.id)) && typeof m.path === 'string') { mdOpen(m.id, m.path); return; }
    if (e.origin !== location.origin || e.source !== frame.contentWindow || !m || m.pm !== 'drawer' || m.act !== 'at') return;
    /* a late message from the page of a chat we already switched away from must not move the highlight back */
    try { if (m.path !== frame.contentWindow.location.pathname) return; } catch (x) { return; }
    href = '/a/' + m.id;
    if (!curHref || !AGENT.test(href)) return;
    if (m.pane && m.pane !== curPane) { curPane = String(m.pane); mark(curHref); }
    if (href === curHref) return;
    curHref = href; setHash(hashOf(href), false); pills(''); d.getElementById('vc-title').textContent = m.id; mark(href);
    d.getElementById('vc-full').href = href;
  });
  window.addEventListener('popstate', sync);
  window.addEventListener('hashchange', sync);
  sync();
  if (!fromHash() && location.hash && /^#(chat|term|plan)=/.test(location.hash)) setHash('', false);
  d.addEventListener('click', function (e) {
    if (e.defaultPrevented || e.button || e.metaKey || e.ctrlKey || e.shiftKey || e.altKey) return;
    var a = e.target.closest('main a[href]');
    if (!a || a.target === '_blank') return;
    var kind = kindOf(a);
    if (!kind) return;
    e.preventDefault(); open(a, kind);
  });
  /* resize: drag the left edge. Kept as the drawer's share of main + drawer (--chat-f, 0..1) in localStorage, so a
     width dragged on a sidebar page is the same split on the Inbox (px would not be); double-click resets to 50% */
  var rz = d.getElementById('vc-resize'), RK = 'pm.v5.chatf', root = d.documentElement;
  function room() {
    var m = drawer.parentElement && drawer.parentElement.querySelector(':scope > main');
    return (m ? m.getBoundingClientRect().width : 0) + drawer.getBoundingClientRect().width;
  }
  function setW(px, save) {
    var tot = room(); if (!(tot > 0)) return;
    px = Math.max(280, Math.min(px, tot * 0.8));
    var f = Math.round(px / tot * 1000) / 1000;
    root.style.setProperty('--chat-f', String(f));
    if (save) { try { localStorage.setItem(RK, String(f)); } catch (e) { /* ignore */ } }
  }
  function resetW() {
    root.style.removeProperty('--chat-f');
    try { localStorage.removeItem(RK); } catch (e) { /* ignore */ }
  }
  d.addEventListener('pm:reset-layout', resetW);
  try { localStorage.removeItem('pm.v5.chatw'); } catch (e) { /* ignore */ }  /* old px key: one width for every page */
  if (rz) {
    rz.addEventListener('pointerdown', function (e) {
      e.preventDefault(); rz.setPointerCapture(e.pointerId); rz.classList.add('on'); d.body.classList.add('vc-dragging');
    });
    rz.addEventListener('pointermove', function (e) {
      if (!rz.hasPointerCapture(e.pointerId)) return;
      setW(drawer.getBoundingClientRect().right - e.clientX, false);
    });
    function end(e) {
      if (!rz.hasPointerCapture(e.pointerId)) return;
      rz.releasePointerCapture(e.pointerId); rz.classList.remove('on'); d.body.classList.remove('vc-dragging');
      setW(drawer.getBoundingClientRect().width, true);
    }
    rz.addEventListener('pointerup', end); rz.addEventListener('pointercancel', end);
    rz.addEventListener('dblclick', resetW);
    rz.addEventListener('keydown', function (e) {
      var k = e.key === 'ArrowLeft' ? 40 : e.key === 'ArrowRight' ? -40 : 0;
      if (k) { e.preventDefault(); setW(drawer.getBoundingClientRect().width + k, true); }
    });
  }
  /* Jump to top / bottom: scroll whatever the framed page scrolls (the window, else the biggest scrollable element). Reads
     frame.contentWindow at click time, so it keeps working after the iframe reloads. */
  function scroller() {
    var w, doc, se, best = null, bh = 0, all, i, el, cs;
    try { w = frame.contentWindow; doc = w.document; se = doc.scrollingElement || doc.documentElement; } catch (e) { return null; }
    if (se && se.scrollHeight > se.clientHeight + 1) return se;
    all = doc.body ? doc.body.querySelectorAll('*') : [];
    for (i = 0; i < all.length; i++) {
      el = all[i];
      if (el.scrollHeight <= el.clientHeight + 1 || el.clientHeight < 80) continue;
      cs = w.getComputedStyle(el);
      if (!/(auto|scroll)/.test(cs.overflowY)) continue;
      if (el.clientHeight > bh) { best = el; bh = el.clientHeight; }
    }
    return best || se;
  }
  function jump(bottom) {
    var el = scroller(); if (!el) return;
    var top = bottom ? el.scrollHeight : 0;
    try { el.scrollTo({ top: top, behavior: 'auto' }); } catch (e) { el.scrollTop = top; }
  }
  /* auto-hide rail: scroll events of the framed page (capture catches inner scrollers) re-show it */
  var jr = window.pmJumpRail && window.pmJumpRail(d.querySelector('#v5chat .jump-rail'), scroller);
  if (jr) {
    frame.addEventListener('load', function () {
      try {
        var w = frame.contentWindow;
        w.addEventListener('scroll', jr.show, { passive: true, capture: true });
        w.document.addEventListener('mousemove', function (e) { if (e.clientX > w.innerWidth - jr.edge) jr.show(); }, { passive: true });
      } catch (e) { /* cross-origin: ignore */ }
      jr.sync();
    });
  }
  d.getElementById('vc-top').addEventListener('click', function () { jump(false); });
  d.getElementById('vc-bottom').addEventListener('click', function () { jump(true); });
  /* wheel over the aside's own header / resize bar (not the framed page, not the md viewer) must not scroll the page behind */
  drawer.addEventListener('wheel', function (e) {
    if (e.target === frame || (e.target.closest && e.target.closest('#v5md-b'))) return;
    e.preventDefault();
  }, { passive: false });
  d.getElementById('vc-close').addEventListener('click', function () { close(); });
  /* Esc closes the topmost layer only: a popover / dialog handled it already (defaultPrevented), then the md popup (full screen
     first), then the aside */
  d.addEventListener('keydown', function (e) {
    if (e.key !== 'Escape' || e.defaultPrevented || d.querySelector('dialog[open]')) return;
    if (mdp && !mdp.hidden) { e.preventDefault(); if (mdp.classList.contains('is-full')) mdFull(false); else mdClose(); return; }
    if (d.body.classList.contains('chat-open')) close();
  });
  /* polled lists re-render rows: keep the active highlight */
  d.body.addEventListener('htmx:afterSettle', function () { if (curHref) mark(curHref); });
  /* nodiff.js merges rows in place (class attribute reset to the server's): re-apply the single selection */
  d.body.addEventListener('nodiff:swapped', function () { if (curHref) mark(curHref); });
  /* Chat aside on every navigation (ONE shared handler, no per-control patches). While the aside is open (body.chat-open + a
     #chat= / #term= / #plan= hash) it must survive whatever the user does: links (tabs, sidebar, sort headers), forms and
     selects (filters, Show, backlog save + redirect), live filters and htmx history pushes.
     1. Any page unload (pagehide) remembers the hash in sessionStorage for a few seconds; drawer-init.js puts it back before
        first paint on the next page that has a drawer (the Projects list has none by design and ignores it).
     2. history.pushState / replaceState without a fragment (live filter, htmx push-url) keep the hash of the open aside. */
  var CK = 'pm.v5.carry';
  function keeps() { return d.body.classList.contains('chat-open') && /^#(chat|term|plan)=/.test(location.hash); }
  window.addEventListener('pagehide', function () {
    if (!keeps()) return;
    try { sessionStorage.setItem(CK, JSON.stringify({ h: location.hash, t: Date.now() })); } catch (e) { /* ignore */ }
  });
  ['pushState', 'replaceState'].forEach(function (fn) {
    var orig = history[fn];
    history[fn] = function (state, title, url) {
      if (url != null && keeps()) {
        try {
          var u = new URL(url, location.href);
          if (u.origin === location.origin && !u.hash) url = u.pathname + u.search + location.hash;
        } catch (e) { /* keep url */ }
      }
      return orig.call(this, state, title, url);
    };
  });
  /* a carried aside landed here: if the link had its own fragment (/#ended-sessions), scroll to it */
  if (window.__pmCarry && window.__pmCarry.anchor && window.__pmCarry.anchor.length > 1) {
    var tgt = null;
    try { tgt = d.getElementById(decodeURIComponent(window.__pmCarry.anchor.slice(1))); } catch (e) { tgt = null; }
    if (tgt && tgt.scrollIntoView) tgt.scrollIntoView();
  }
  /* Phones: the aside is an iframe, so the chat's own visualViewport never shrinks when the iOS keyboard opens.
     Fit the aside to the parent's visual viewport (no page showing under the composer) and tell the chat to go compact. */
  (function () {
    var vv = window.visualViewport, narrow = window.matchMedia('(max-width:699px)'), r = d.documentElement, raf = 0, base = 0;
    if (!vv) return;
    function fit() {
      raf = 0;
      var on = narrow.matches && d.body.classList.contains('chat-open');
      if (!on) { r.style.removeProperty('--vvt'); r.style.removeProperty('--vvh'); base = 0; return; }
      if (!base || vv.height > base) base = vv.height;
      r.style.setProperty('--vvt', Math.round(vv.offsetTop) + 'px');
      r.style.setProperty('--vvh', Math.round(vv.height) + 'px');
      var k = base - vv.height > 100;
      try { var fb = frame.contentDocument && frame.contentDocument.body; if (fb) fb.classList.toggle('kbd', k); } catch (e) { /* cross-origin or reloading */ }
    }
    function sched() { if (!raf) raf = window.requestAnimationFrame(fit); }
    vv.addEventListener('resize', sched); vv.addEventListener('scroll', sched);
    frame.addEventListener('load', sched);
    window.addEventListener('orientationchange', function () { base = 0; sched(); });
    new MutationObserver(sched).observe(d.body, { attributes: true, attributeFilter: ['class'] });
  })();
})();
