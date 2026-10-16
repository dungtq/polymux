/* v5 sidebar: expand / collapse (desktop), slide-in button (phone). The current row is server-rendered. */
(function () {
  var d = document, h = d.documentElement, side = d.getElementById('v5side'), main = d.querySelector('main');
  if (!side || !main) return;
  /* Collapsed / fold are per tab (sessionStorage: kept across reload and navigation in this tab, a new tab starts from the
     defaults). The dragged width is a saved preference (localStorage), shared by every tab and kept after restart. */
  function ld(k) { try { return sessionStorage.getItem(k); } catch (e) { return null; } }
  function sv(k, v) { try { if (v == null) sessionStorage.removeItem(k); else sessionStorage.setItem(k, v); } catch (e) { /* ignore */ } }
  function gsv(k, v) { try { if (v == null) localStorage.removeItem(k); else localStorage.setItem(k, v); } catch (e) { /* ignore */ } }
  var KEY = 'pm.v5.side', tg = side.querySelector('.vs-toggle');
  function setCol(c, save) {
    h.classList.toggle('side-collapsed', c);
    if (tg) {
      var t = c ? 'Expand sidebar' : 'Collapse sidebar';
      tg.setAttribute('aria-expanded', String(!c)); tg.setAttribute('aria-label', t); tg.title = t;
    }
    if (save) { sv(KEY, c ? '1' : '0'); }
  }
  setCol(h.classList.contains('side-collapsed'), false);
  if (tg) tg.addEventListener('click', function () { setCol(!h.classList.contains('side-collapsed'), true); });

  /* Foot (Manage / Service / Account) collapse: saved preference in localStorage (a restricted browser just starts expanded). */
  var FOOTKEY = 'pm.v5.sidefoot', ftg = side.querySelector('.vs-foot-tg'), foot = side.querySelector('.vs-foot');
  function setFoot(c, save) {
    if (!ftg || !foot) return;
    foot.classList.toggle('vs-foot-c', c);
    var t = c ? 'Expand Manage, Service and Account' : 'Collapse Manage, Service and Account';
    ftg.setAttribute('aria-expanded', String(!c)); ftg.title = t;
    var lb = ftg.querySelector('span'); if (lb) lb.textContent = c ? 'Expand' : 'Collapse';
    if (save) gsv(FOOTKEY, c ? '1' : '0');
  }
  try { setFoot(localStorage.getItem(FOOTKEY) === '1', false); } catch (e) { /* ignore */ }
  if (ftg) ftg.addEventListener('click', function () { setFoot(!foot.classList.contains('vs-foot-c'), true); });

  /* Unread ntfy badges on the project / tmux session rows, and the status dots of the project / agent rows (data-dot-kind):
     refreshed on the same events as the Inbox badge, and every minute. Added / removed rows still need a reload. */
  var bl = side.querySelector('[data-badge-kind]'), dl = side.querySelector('[data-dot-kind]'), DOTS = /^[a-z]+$/;
  function setDot(row, dot) {
    var el = row.querySelector(':scope > .dot, :scope > .vs-ps, :scope > .vs-sp'), sr;
    if (!el || (dot && !DOTS.test(dot))) return;
    if (!dot) {
      if (!el.classList.contains('dot')) return;
      sr = el.nextElementSibling; if (sr && sr.classList.contains('sr-only')) sr.remove();
      var sp = d.createElement('span'); sp.className = row.classList.contains('vs-prow') ? 'vs-ps' : 'vs-sp';
      el.replaceWith(sp); return;
    }
    if (!el.classList.contains('dot')) {
      var dt = d.createElement('span'); sr = d.createElement('span'); sr.className = 'sr-only';
      el.replaceWith(dt); dt.after(sr); el = dt;
    }
    el.className = 'dot dot-' + dot; el.title = dot;
    sr = el.nextElementSibling; if (sr && sr.classList.contains('sr-only')) sr.textContent = dot;
  }
  function dots() {
    if (!dl) return;
    var k = dl.getAttribute('data-dot-kind'), attr = k === 'agents' ? 'data-a' : 'data-p', rs = dl.querySelectorAll('.vs-row[' + attr + ']');
    if (!rs.length) return;
    var u = '/side/dots?kind=' + encodeURIComponent(k) + (k === 'agents' ? '&agent=' + encodeURIComponent(dl.getAttribute('data-dot-agent') || '') : '');
    window.pm.fetch(u, { credentials: 'same-origin', headers: { Accept: 'application/json' } })
      .then(function (r) { return r.ok ? r.json() : null; })
      .then(function (m) {
        if (!m) return;
        rs.forEach(function (row) { var key = row.getAttribute(attr); if (Object.prototype.hasOwnProperty.call(m, key)) setDot(row, m[key]); });
      }).catch(function () { /* ignore */ });
  }
  function badges() {
    dots();
    if (!bl) return;
    window.pm.fetch('/side/unread?kind=' + bl.getAttribute('data-badge-kind'), { credentials: 'same-origin', headers: { Accept: 'application/json' } })
      .then(function (r) { return r.ok ? r.json() : null; })
      .then(function (m) {
        if (!m) return;
        bl.querySelectorAll('.vs-row[data-p]').forEach(function (row) {
          var b = row.querySelector('[data-ub]');
          if (b) window.pm.badges.set(b, m[row.getAttribute('data-p')] || 0);
        });
      }).catch(function () { /* ignore */ });
  }
  if (bl || dl) window.pm.badges.on(badges);  // same triggers as the Inbox badge (core.js pm.badges)

  // Resizable width: handle is injected here (not in _side.html). Desktop only (>=900px); CSS reads --side-user.
  var WK = 'pm.v5.sidew', DEF = 248, MIN = 180, STEP = 16, mq = window.matchMedia('(min-width:900px)'), grip = d.createElement('div');
  grip.className = 'vs-grip'; grip.setAttribute('role', 'separator'); grip.setAttribute('aria-orientation', 'vertical');
  grip.setAttribute('aria-label', 'Resize sidebar'); grip.tabIndex = 0; grip.title = 'Drag to resize, double-click to reset';
  side.parentNode.insertBefore(grip, side.nextSibling);
  function maxW() { return Math.max(MIN, Math.floor(window.innerWidth * 0.4)); }
  function clamp(w) { return Math.min(maxW(), Math.max(MIN, Math.round(w))); }
  function cur() { var w = parseInt(h.style.getPropertyValue('--side-user'), 10); return w >= MIN ? clamp(w) : DEF; }
  function aria(w) {
    grip.setAttribute('aria-valuemin', String(MIN)); grip.setAttribute('aria-valuemax', String(maxW()));
    grip.setAttribute('aria-valuenow', String(w));
  }
  function setW(w, save) {
    if (w == null) { h.style.removeProperty('--side-user'); aria(DEF); if (save) { gsv(WK, null); } return; }
    w = clamp(w); h.style.setProperty('--side-user', w + 'px'); aria(w);
    if (save) { gsv(WK, String(w)); }
  }
  aria(cur());
  grip.addEventListener('pointerdown', function (e) {
    if (e.button || !mq.matches) return;
    e.preventDefault(); grip.setPointerCapture(e.pointerId); grip.classList.add('drag'); d.body.classList.add('vs-dragging');
    var x0 = e.clientX, w0 = cur();
    function mv(ev) { setW(w0 + ev.clientX - x0, false); }
    function up() {
      grip.removeEventListener('pointermove', mv); grip.removeEventListener('pointerup', up); grip.removeEventListener('pointercancel', up);
      grip.classList.remove('drag'); d.body.classList.remove('vs-dragging'); setW(cur(), true);
    }
    grip.addEventListener('pointermove', mv); grip.addEventListener('pointerup', up); grip.addEventListener('pointercancel', up);
  });
  grip.addEventListener('dblclick', function () { setW(null, true); });
  grip.addEventListener('keydown', function (e) {
    var w = cur(), k = e.key;
    if (k === 'ArrowLeft') w -= STEP; else if (k === 'ArrowRight') w += STEP;
    else if (k === 'Home') w = MIN; else if (k === 'End') w = maxW();
    else if (k === 'Enter' || k === 'Escape') { if (k === 'Enter') { e.preventDefault(); setW(null, true); } return; }
    else return;
    e.preventDefault(); setW(w, true);
  });
  window.addEventListener('resize', function () { if (h.style.getPropertyValue('--side-user')) setW(cur(), false); else aria(DEF); });

  /* Project list: the server already rendered open / collapsed for this page (no flash). A manual toggle is saved per tab as
     "<p|o>:<0|1>" (p = Projects / project pages, o = other pages) and only applies to the same kind of page; side-init.js
     showed it pre-paint through html.pj-*, which is dropped here once the aside carries the state itself. */
  var pt = side.querySelector('.vs-pt:not(.vs-it)'), PK = 'pm.v5.projopen', here = /^\/p(\/|$)/.test(location.pathname) ? 'p' : 'o';
  function setProj(o, save) {
    side.classList.toggle('vs-pclosed', !o);
    if (pt) { var t = (o ? 'Collapse' : 'Expand') + ' project list'; pt.setAttribute('aria-expanded', String(o)); pt.title = t; pt.setAttribute('aria-label', t); }
    if (save) sv(PK, here + ':' + (o ? '1' : '0'));
  }
  if (pt) {
    var pv = ld(PK);
    if (pv && pv.charAt(0) === here && pv.charAt(1) === ':') setProj(pv.charAt(2) === '1', false);
    h.classList.remove('pj-0', 'pj-1');
    pt.addEventListener('click', function () { setProj(side.classList.contains('vs-pclosed'), true); });
  }

  /* The Settings popup (sidebar foot): its panel has its own htmx forms; dialog.js closes a dialog after any request inside it, so
     requests from within this one are not passed on to it. */
  var sd = d.getElementById('settings-dialog');
  if (sd) sd.addEventListener('htmx:afterRequest', function (e) { e.stopPropagation(); });

  /* Inbox views fold: the server rendered open (Inbox pages) or folded (elsewhere). A manual toggle is saved per tab as
     "<i|o>:<0|1>" and only applies to the same kind of page; side-init.js showed it pre-paint through html.ib-*. */
  var it = side.querySelector('.vs-it'), IK = 'pm.v5.inboxopen', ihere = location.pathname === '/' ? 'i' : 'o';
  function setInbox(o, save) {
    side.classList.toggle('vs-iclosed', !o);
    if (it) { var t = (o ? 'Collapse' : 'Expand') + ' Inbox views'; it.setAttribute('aria-expanded', String(o)); it.title = t; it.setAttribute('aria-label', t); }
    if (save) sv(IK, ihere + ':' + (o ? '1' : '0'));
  }
  if (it) {
    var iv = ld(IK);
    if (iv && iv.charAt(0) === ihere && iv.charAt(1) === ':') setInbox(iv.charAt(2) === '1', false);
    h.classList.remove('ib-0', 'ib-1');
    it.addEventListener('click', function () { setInbox(side.classList.contains('vs-iclosed'), true); });
  }

  // Reset layout (Settings > Appearance, button [data-layout-reset], in-page confirm): sidebar width, collapsed and fold back to the
  // defaults, and the chat drawer split (drawer.js listens; its saved width is cleared here too, the drawer is not on every page).
  function resetLayout() {
    setW(null, true); setCol(false, true); sv(IK, null);
    gsv('pm.v5.chatf', null);
    d.dispatchEvent(new CustomEvent('pm:reset-layout'));
    var n = d.querySelector('[data-layout-reset-msg]'); if (n) n.textContent = 'Layout reset to the defaults.';
  }
  d.addEventListener('click', function (e) {
    if (!(e.target.closest && e.target.closest('[data-layout-reset]'))) return;
    var q = { title: 'Reset the layout?', body: 'Sidebar width, collapsed state and chat width go back to the defaults.', confirmLabel: 'Reset' };
    if (window.pmConfirm) window.pmConfirm(q).then(function (ok) { if (ok) resetLayout(); });
  });

  // The filter box is the shared one (filterbox(scope="#v5side") + static/js/filter.js): name-type data-q per row.

  function showCurrent() {
    var cur = side.querySelector('.vs-row.on');
    if (cur && side.scrollHeight > side.clientHeight) side.scrollTop = Math.max(0, cur.offsetTop - side.clientHeight / 2);
  }
  showCurrent();
  var lab = side.getAttribute('data-label') || 'Projects', b = d.createElement('button');
  b.type = 'button'; b.className = 'v5-sidebtn'; b.setAttribute('aria-label', 'Show ' + lab.toLowerCase());
  b.innerHTML = '<svg viewBox="0 0 24 24" width="16" height="16" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" aria-hidden="true"><path d="M4 6h16M4 12h16M4 18h16"/></svg>';
  b.appendChild(d.createTextNode(lab));
  main.insertBefore(b, main.firstChild);
  b.addEventListener('click', function () { d.body.classList.toggle('side-open'); showCurrent(); });
  d.addEventListener('click', function (e) {
    if (d.body.classList.contains('side-open') && !e.target.closest('.v5-side,.v5-sidebtn')) d.body.classList.remove('side-open');
  });
})();
