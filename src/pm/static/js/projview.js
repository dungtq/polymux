// Project page right-hand panel: Agents (back to the tables) / Browse / Git / Plans / TODO / Backlog / README / FEATURES / CHANGELOG / CLAUDE / AGENTS tabs and read-only file links
// (skills, project files tree). GET-only; the server returns already-masked, escaped HTML.
(function () {
  var pane = document.getElementById('pj-right'), body = document.getElementById('pj-body');
  if (!pane || !body) return;
  var extra = document.querySelector('[data-pj-x]'), browse = document.querySelector('[data-pj="browse"]'), seq = 0;
  function select(t) { if (t && t.closest('#pj-tabbar')) window.pm.tabs.select(t, { silent: true }); }   /* doc tabs (#pj-tabbar .pj-tab): pm.tabs marks and centres */
  /* Git tab sub-tab (Git status | History | Config): pm.tabs selects and remembers it (data-tabs-key pm.pjgit.sub) */
  function subtab(t, restored) {
    var g = body.querySelector('.pj-git'), list = g && g.querySelector('[role=tablist]');
    if (!list) return;
    t = t || window.pm.tabs.restore(list);
    var w = t ? t.getAttribute('data-tab') : 'history';
    g.querySelectorAll('[data-subpanel]').forEach(function (s) { s.hidden = s.getAttribute('data-subpanel') !== w; });
    var sp = g.querySelector('[data-subpanel="status"]');
    if (w === 'status' && sp && sp.getAttribute('data-src') && !(restored && sp.hasAttribute('data-loaded'))) {   /* dirty list is fetched on each open (not on a live refresh) */
      sp.setAttribute('data-loaded', '');
      window.pm.fetch(sp.getAttribute('data-src'), { credentials: 'same-origin', headers: { 'X-Requested-With': 'pm' } })
        .then(function (r) { return r.ok ? r.text() : Promise.reject(); })
        .then(function (h) { sp.innerHTML = h; })
        .catch(function () { sp.innerHTML = '<p class="muted pj-note">Could not load.</p>'; });
    }
  }
  body.addEventListener('pm:tab', function (e) {
    if (e.target.closest('.pj-git')) subtab(e.detail.tab, e.detail.restored);
  });
  function stash() {   /* Plans / TODO / Backlog panes are page sections moved into the body; park them before the body is replaced */
    var h = document.getElementById('pj-panes');
    if (h) body.querySelectorAll('.pj-pane').forEach(function (n) { n.hidden = true; h.appendChild(n); });
  }
  function load(url, t) {
    var n = ++seq;
    select(t);
    body.setAttribute('aria-busy', 'true');
    window.pm.fetch(url, { credentials: 'same-origin', headers: { 'X-Requested-With': 'pm' } })
      .then(function (r) { return r.text().then(function (h) { return { ok: r.ok, h: h }; }); })
      .then(function (r) {
        if (n !== seq) return;
        var ot = body.querySelector('.pj-ftree'), ots = ot ? ot.scrollTop : 0;   /* keep the tree where it was, so a click does not jump it to the top */
        stash();
        body.innerHTML = r.h;   /* server-escaped fragment; no script runs from innerHTML */
        body.scrollTop = 0;
        var fv = body.querySelector('.pj-fview'); if (fv) { fv.scrollTop = 0; fv.scrollLeft = 0; }
        var nt = body.querySelector('.pj-ftree'); if (nt && ots) nt.scrollTop = ots;   /* the Files pane scrolls in .pj-fview, not in the body */
        if (window.htmx) window.htmx.process(body);   /* the Git tab carries hx-* forms */
        subtab();
        if (t.getAttribute('data-pj') === 'settings' && !ctx()) pane.scrollIntoView({ block: 'start' });   /* phones: show the loaded settings under the header, not far below Stack */
      })
      .catch(function () {
        if (n !== seq) return;
        stash();
        body.innerHTML = '<p class="muted pj-note">Could not load.</p>';
      })
      .then(function () { if (n === seq) body.removeAttribute('aria-busy'); });
  }
  var agentsTab = document.querySelector('[data-pj-agents]'), lastDoc = document.querySelector('#pj-tabbar .pj-tab[aria-selected="true"]');
  var wide = window.matchMedia('(min-width:700px)'), sbs = window.matchMedia('(min-width:1000px)');
  /* docs REPLACE the agent tables when they cannot sit beside them (chat aside open, or 700-999px); the Agents tab brings the tables back */
  function ctx() { return !wide.matches || document.body.classList.contains('chat-open') || !sbs.matches; }   /* phones (<700px) always replace */
  function sync() {
    var c = ctx();
    split.classList.toggle('pj-repl', c);
    if (!c) { split.classList.remove('pj-docsview'); if (agentsTab && agentsTab.getAttribute('aria-selected') === 'true' && lastDoc) select(lastDoc); }
    else if (!split.classList.contains('pj-docsview') && agentsTab) select(agentsTab);
  }
  var split = pane.closest('.pj-split'), show = split && split.querySelector('[data-pj-show]'), key = 'pm.pjdocs.hidden.' + (split && split.getAttribute('data-proj'));
  function setHidden(h, save) {
    if (!split) return;
    split.classList.toggle('pj-nodocs', h);
    if (show) show.hidden = !h;
    if (save) { try { if (h) localStorage.setItem(key, '1'); else localStorage.removeItem(key); } catch (e) {} }
  }
  try { if (localStorage.getItem(key) === '1') setHidden(true, false); } catch (e) {}
  var cl = document.querySelector('[data-pj-close]');
  if (cl) cl.addEventListener('click', function () { split.classList.remove('pj-docsview'); setHidden(true, true); sync(); });
  if (agentsTab) agentsTab.addEventListener('click', function (e) { e.preventDefault(); e.stopPropagation(); split.classList.remove('pj-docsview'); select(agentsTab); });
  [wide, sbs].forEach(function (m) { if (m.addEventListener) m.addEventListener('change', sync); });
  new MutationObserver(sync).observe(document.body, { attributes: true, attributeFilter: ['class'] });
  sync();
  if (show) show.addEventListener('click', function () { setHidden(false, true); });
  document.addEventListener('click', function (e) {
    if (e.defaultPrevented || e.button || e.metaKey || e.ctrlKey || e.shiftKey || e.altKey) return;
    var pa = e.target.closest('a[data-pj-pane]');
    if (!pa) return;
    var el = document.getElementById(pa.getAttribute('data-pj-pane'));
    if (!el) return;
    e.preventDefault();
    seq++;
    stash();
    body.textContent = '';
    el.hidden = false;
    body.appendChild(el);
    body.scrollTop = 0;
    select(pa);
    lastDoc = pa;
    if (ctx()) split.classList.add('pj-docsview');
  });
  document.addEventListener('click', function (e) {
    if (e.defaultPrevented || e.button || e.metaKey || e.ctrlKey || e.shiftKey || e.altKey) return;
    var a = e.target.closest('a[data-pj], a[data-pj-open], a[data-pj-x], a[data-pj-in]');
    if (!a) return;
    e.preventDefault();
    var t = a;
    if (a.hasAttribute('data-pj-in')) t = browse || extra;   /* stays in the Browse tab */
    if (a.hasAttribute('data-pj-open')) {
      t = extra;
      extra.textContent = a.getAttribute('data-title') || 'File';
      extra.setAttribute('href', a.getAttribute('href'));
      extra.hidden = false;
    } else if (a === extra) {
      t = extra;
    }
    openTab(a.getAttribute('href'), t);
  });
  function openTab(url, t) {
    if (split.classList.contains('pj-nodocs')) setHidden(false, true);   /* the settings gear and quick links reopen a closed panel */
    if (ctx()) split.classList.add('pj-docsview');   /* docs replace the agent tables, own scroll (phones too) */
    if (t !== extra && t !== agentsTab) lastDoc = t;
    load(url, t);
  }
  function openExtra(url, title) {
    extra.textContent = title;
    extra.setAttribute('href', url);
    extra.hidden = false;
    openTab(url, extra);
  }
  /* redirects such as /p/x?added=1#backlog open that pane; a tracker project's Issues tab answers #backlog too (#issue=N: issues.js) */
  var hp = location.hash.match(/^#(plans|todo|backlog|issues)$/);
  if (hp) {
    var ht = document.querySelector('a[data-pj-pane="pj-p-' + hp[1] + '"]');
    if (!ht && /^(backlog|issues)$/.test(hp[1])) ht = document.querySelector('a[data-pj-pane="pj-p-issues"], a[data-pj-pane="pj-p-backlog"]');
    if (ht) ht.click();
  }
  /* A direct navigation to a pane fragment (/p/x/files?path=a, /p/x/view/readme, /p/x/skill/...) is 303'd by the server to
     /p/x#browse=a, #view=readme or #skill=scope/src/name: open that tab here through the same loader. */
  var hf = location.hash.match(/^#(browse|view|skill)(?:=(.*))?$/), pb = location.pathname.replace(/\/+$/, '');
  if (hf) {
    var hv = '';
    try { hv = hf[2] ? decodeURIComponent(hf[2]) : ''; } catch (e) {}
    if (hf[1] === 'browse') {
      var bu = pb + '/files' + (hv ? '?path=' + encodeURIComponent(hv) : '');
      if (browse) openTab(bu, browse); else openExtra(bu, 'Browse');
    } else if (hf[1] === 'view' && /^[a-z]+$/.test(hv)) {
      var vt = document.querySelector('#pj-tabbar .pj-tab[data-pj="' + hv + '"]');
      if (vt) openTab(vt.getAttribute('href'), vt); else openExtra(pb + '/view/' + hv, hv.toUpperCase());
    } else if (hf[1] === 'skill') {
      var sk = hv.match(/^([a-z]+)\/([a-z]+)\/([^/]+)$/);
      if (sk) openExtra(pb + '/skill/' + sk[1] + '/' + sk[2] + '/' + encodeURIComponent(sk[3]), sk[3]);
    }
  }
  /* With the chat aside open a plan link in the Plans tab shows the plan inside the tab, full height (embedded plan page);
     without it drawer.js opens the plan in the aside. Capture phase: runs before drawer.js. */
  var pv = document.querySelector('#pj-p-plans .pj-planview'), pvf = pv && pv.querySelector('iframe'), plist = pv && pv.parentNode;
  function pvFit() { if (pv && !pv.hidden) pvf.style.height = Math.max(240, window.innerHeight - pvf.getBoundingClientRect().top - 12) + 'px'; }
  function pvShow(on) {
    if (!pv) return;
    Array.prototype.forEach.call(plist.children, function (n) { if (n !== pv) n.hidden = on; });
    pv.hidden = !on;
    if (on) pvFit(); else pvf.src = 'about:blank';
  }
  document.addEventListener('click', function (e) {
    if (!pv || e.defaultPrevented || e.button || e.metaKey || e.ctrlKey || e.shiftKey || e.altKey) return;
    if (e.target.closest('[data-planview-back]')) { pvShow(false); return; }
    var a = e.target.closest('#pj-p-plans a[href]');
    if (!a || !document.body.classList.contains('chat-open') || !/^\/p\/[^/?#]+\/plans\/[^/?#]+/.test(a.getAttribute('href'))) return;
    e.preventDefault(); e.stopPropagation();
    pvShow(true);
    pvf.src = a.getAttribute('href') + (a.getAttribute('href').indexOf('?') < 0 ? '?' : '&') + 'embed=1';
    pvFit();
  }, true);
  window.addEventListener('resize', pvFit);
  new MutationObserver(function () { if (!document.body.classList.contains('chat-open')) pvShow(false); }).observe(document.body, { attributes: true, attributeFilter: ['class'] });
  /* Backlog tab: "Edit file" edits BACKLOG.md in a framed editor inside the tab; Cancel / a finished save (postMessage from
     backlog.js in the frame) close it, a save also reloads the page so the tab shows the new items. */
  var be = document.querySelector('#pj-p-backlog .pj-bledit'), bef = be && be.querySelector('iframe');
  function beFit() { if (be && !be.hidden) bef.style.height = Math.max(300, window.innerHeight - bef.getBoundingClientRect().top - 12) + 'px'; }
  function beShow(on) {
    if (!be) return;
    Array.prototype.forEach.call(be.parentNode.children, function (n) { if (n !== be) n.style.display = on ? 'none' : ''; });
    be.hidden = !on;
    if (on) beFit(); else bef.src = 'about:blank';
  }
  document.addEventListener('click', function (e) {
    var b = e.target.closest('[data-bl-file]');
    if (!b || !be) return;
    e.preventDefault();
    beShow(true);
    bef.src = b.getAttribute('data-bl-file');
    beFit();
  });
  window.addEventListener('message', function (e) {
    if (e.origin !== location.origin || !e.data || e.data.pm !== 'backlog-edit-close') return;
    beShow(false);
    if (e.data.saved) { location.hash = '#backlog'; location.reload(); }
  });
  window.addEventListener('resize', beFit);
})();
/* Skills tab: Local / Global sub-tabs (delegated, the panel is loaded on select; pm.tabs marks the tab) */
document.addEventListener('click', function (ev) {
  var t = ev.target.closest && ev.target.closest('[data-sk-tab]');
  if (!t) return;
  var root = t.closest('.pj-skills');
  if (!root) return;
  window.pm.tabs.select(t, { silent: true });
  root.querySelectorAll('.pj-skgrp').forEach(function (g) { g.hidden = g.getAttribute('data-scope') !== t.getAttribute('data-sk-tab'); });
});
