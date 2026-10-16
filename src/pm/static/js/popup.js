/* The one popover layer for pm (docs/UI-RULES.md "Controls"): anchored menus and popovers, phone bottom sheets, and the
   <details> dropdowns that float over the page. Modal dialogs are dialog.js (pmDialog); this file never opens one.
   pm.popover.open(anchor, el, {placement: 'bottom-start'|'bottom-end'|'top-start'|'top-end', flip: true,
     role: 'menu'|'listbox'|'dialog'|null, focus: 'first'|'none', sheet: false, onClose: fn(el)})
     Shows el (removes [hidden]) as position:fixed next to anchor (CSSOM only: the CSP refuses style= markup), flips to the
     other side when there is no room and clamps it into the viewport; follows resize and scroll. Under 700px an el with
     data-sheet (or opts.sheet) becomes a bottom sheet (.pop.is-sheet, placed by popup.css). Sets aria-expanded and
     aria-controls on the anchor, moves focus in (default 'first' for role=menu) and back to the anchor on close.
     Up/Down/Home/End move between the items of a role=menu layer.
   pm.popover.close(el?) closes el (default: the topmost layer); toggle(anchor, el, opts); isOpen(el).
   One layer stack: Escape closes only the topmost layer (never while a modal dialog is open over it, never when another
   handler already took the key); a pointerdown outside every open layer closes it (a pointerdown on the anchor counts as
   inside, so the opening tap never closes it again). Opening a layer closes the open ones that do not hold its anchor.
   <details> dropdowns (data-pop, .acct, .tab-stats, .dt-pop, .newsess) join the stack when they open; they open and close
   natively without JS and keep their CSS position. A pointerdown here also tells the parent page and same-origin child
   iframes (the chat drawer) so a click in one document closes the popovers of the other. External file (strict CSP). */
(function () {
  'use strict';
  var pm = window.pm = window.pm || {}, d = document, stack = [], seq = 0, queued = false;
  var DETAILS = 'details[data-pop], details.acct, details.tab-stats, details.dt-pop, details.newsess';
  var OPEN_DETAILS = DETAILS.replace(/(, |$)/g, '[open]$1');
  var ITEMS = 'a[href], button:not([disabled]), input:not([type=hidden]):not([disabled]), select:not([disabled]), textarea:not([disabled]), [tabindex]:not([tabindex="-1"])';
  var mq = window.matchMedia ? window.matchMedia('(max-width: 699px)') : { matches: false };
  var M = 8, GAP = 4;  // viewport margin, anchor gap (px)

  function prune() {  // layers whose element left the page (an htmx swap) never get a toggle / close: drop them
    for (var i = stack.length - 1; i >= 0; i--) if (!d.contains(stack[i].el)) stack.splice(i, 1);
  }
  function find(el) { for (var i = stack.length - 1; i >= 0; i--) if (stack[i].el === el) return i; return -1; }
  function box(L) { return L.details ? (L.el.querySelector(':scope > :not(summary)') || L.el) : L.el; }
  function inside(L, t) { return !!t && (L.el.contains(t) || (!!L.anchor && L.anchor.contains(t))); }
  function roleOf(L) { return L.opts.role || box(L).getAttribute('role') || ''; }
  function visible(n) { return !!(n.offsetWidth || n.offsetHeight || n.getClientRects().length); }
  function items(L) {
    var b = box(L), list = b.querySelectorAll('[role=menuitem], [role=menuitemradio], [role=menuitemcheckbox]');
    if (!list.length) list = b.querySelectorAll(ITEMS);
    return Array.prototype.filter.call(list, visible);
  }
  function unplace(el) {
    ['position', 'top', 'left', 'right', 'bottom'].forEach(function (k) { el.style[k] = ''; });
    el.removeAttribute('data-placement');
  }

  /* fixed position next to the anchor: flip to the side with more room when it does not fit, then clamp */
  function place(L) {
    var b = box(L), o = L.opts, sheet = !!(o.sheet || b.hasAttribute('data-sheet')) && mq.matches;
    b.classList.toggle('is-sheet', sheet);
    if (L.details || sheet || !L.anchor) { if (!L.details) unplace(b); return; }  // <details> and sheets: CSS places them
    var r = L.anchor.getBoundingClientRect(), vw = d.documentElement.clientWidth, vh = window.innerHeight;
    var p = o.placement || 'bottom-start', up = p.indexOf('top') === 0, end = /-end$/.test(p);
    b.style.position = 'fixed'; b.style.right = 'auto'; b.style.bottom = 'auto'; b.style.top = '0px'; b.style.left = '0px';
    var w = b.offsetWidth, h = b.offsetHeight, below = vh - r.bottom - GAP - M, above = r.top - GAP - M;
    if (o.flip !== false && (up ? h > above && below > above : h > below && above > below)) up = !up;
    var x = end ? r.right - w : r.left, y = up ? r.top - GAP - h : r.bottom + GAP;
    b.style.left = Math.round(Math.max(M, Math.min(x, vw - M - w))) + 'px';
    b.style.top = Math.round(Math.max(M, Math.min(y, vh - M - h))) + 'px';
    b.setAttribute('data-placement', (up ? 'top' : 'bottom') + (end ? '-end' : '-start'));
  }

  function open(anchor, el, opts) {
    if (!el) return el;
    var i = find(el);
    if (i > -1) { place(stack[i]); return el; }
    for (var j = stack.length - 1; j >= 0; j--) if (!inside(stack[j], anchor || el)) closeAt(j, false);
    var L = { anchor: anchor || null, el: el, opts: opts || {}, details: el.tagName === 'DETAILS' };
    stack.push(L);
    var b = box(L);
    if (L.opts.role) b.setAttribute('role', L.opts.role);
    if (L.details) { if (!el.open) el.open = true; } else {
      el.hidden = false;
      if (anchor) {
        if (!b.id) b.id = 'pmpop-' + (++seq);
        anchor.setAttribute('aria-expanded', 'true'); anchor.setAttribute('aria-controls', b.id);
      }
    }
    place(L);
    if ((L.opts.focus || (roleOf(L) === 'menu' ? 'first' : 'none')) === 'first') {
      var it = items(L)[0];
      if (it) it.focus({ preventScroll: true });
    }
    return el;
  }

  function closeAt(i, refocus) {
    var L = stack[i];
    if (!L) return;
    stack.splice(i, 1);
    var b = box(L), had = L.el.contains(d.activeElement);
    if (L.details) L.el.open = false; else { L.el.hidden = true; unplace(b); }
    b.classList.remove('is-sheet');
    if (L.anchor && !L.details) L.anchor.setAttribute('aria-expanded', 'false');
    var back = L.details ? L.el.querySelector('summary') : L.anchor;
    if (refocus && had && back && back.focus) back.focus({ preventScroll: true });
    if (typeof L.opts.onClose === 'function') L.opts.onClose(L.el);
  }

  /* everything not holding target t (null: everything), plus open <details> popovers that never joined the stack */
  function closeAll(t, refocus) {
    for (var i = stack.length - 1; i >= 0; i--) if (!inside(stack[i], t)) closeAt(i, refocus);
    Array.prototype.forEach.call(d.querySelectorAll(OPEN_DETAILS), function (x) {
      if (find(x) < 0 && !(t && x.contains(t))) x.open = false;
    });
  }

  function close(el) {
    if (!el) { closeAt(stack.length - 1, true); return; }
    var i = find(el);
    if (i > -1) closeAt(i, true); else if (el.tagName === 'DETAILS') el.open = false; else el.hidden = true;
  }
  function isOpen(el) { return find(el) > -1; }
  function toggle(anchor, el, opts) { if (isOpen(el)) close(el); else open(anchor, el, opts); return isOpen(el); }
  pm.popover = { open: open, close: close, toggle: toggle, isOpen: isOpen };

  /* <details> dropdowns join (and leave) the stack on their native toggle; toggle does not bubble, so listen in capture */
  d.addEventListener('toggle', function (e) {
    var x = e.target;
    if (!x || x.tagName !== 'DETAILS' || !x.matches(DETAILS)) return;
    var i = find(x);
    if (x.open && i < 0) open(x.querySelector('summary'), x, {});
    else if (!x.open && i > -1) closeAt(i, false);
  }, true);

  function rove(L, e) {
    var list = items(L), a = d.activeElement, i = list.indexOf(a);
    if (!list.length || (i < 0 && !inside(L, a))) return;  // focus is elsewhere: leave the keys alone
    e.preventDefault();
    var k = e.key, n = k === 'Home' ? 0 : k === 'End' ? list.length - 1 : k === 'ArrowDown' ? (i + 1) % list.length : (i <= 0 ? list.length - 1 : i - 1);
    list[n].focus();
  }
  function adopt() {  // an open <details> whose toggle event has not run yet (a key pressed right after the click)
    Array.prototype.forEach.call(d.querySelectorAll(OPEN_DETAILS), function (x) {
      if (find(x) < 0) open(x.querySelector('summary'), x, { focus: 'none' });
    });
  }
  d.addEventListener('keydown', function (e) {
    if (e.defaultPrevented || e.isComposing || !/^(Escape|ArrowDown|ArrowUp|Home|End)$/.test(e.key)) return;
    prune(); adopt();
    var L = stack[stack.length - 1];
    if (!L) return;
    if (e.key === 'Escape') {
      var dlg = d.querySelector('dialog[open]');
      if (dlg && !dlg.contains(L.el)) return;  // the modal on top takes Esc (native cancel)
      e.preventDefault(); e.stopImmediatePropagation();  // only the topmost layer closes (not the drawer as well)
      closeAt(stack.length - 1, true);
    } else if (roleOf(L) === 'menu') rove(L, e);
  });

  /* outside tap / click. Capture phase on pointerdown only (as before, so a handler that stops pointerdown cannot keep a
     popover open); no click handler here, so the capture-phase click handlers in dialog.js / rowx.js keep their order. */
  /* same-origin windows only: a sandboxed (opaque-origin) preview frame would log a target-origin mismatch error */
  function sameOrigin(w) { try { return w.location.origin === location.origin; } catch (x) { return false; } }
  function tell(w) { try { if (sameOrigin(w)) w.postMessage({ pm: 'outclick' }, location.origin); } catch (x) { /* detached */ } }
  function frames() { return d.querySelectorAll('iframe'); }
  d.addEventListener('pointerdown', function (e) {
    closeAll(e.target, false);
    if (window.parent && window.parent !== window) tell(window.parent);
    Array.prototype.forEach.call(frames(), function (f) { if (f.contentWindow) tell(f.contentWindow); });
  }, true);
  window.addEventListener('message', function (e) {
    var m = e.data;
    if (e.origin !== location.origin || !m || m.pm !== 'outclick' || e.source === window) return;
    if (e.source !== window.parent && !Array.prototype.some.call(frames(), function (f) { return f.contentWindow === e.source; })) return;
    closeAll(null, false);
  });

  /* keep anchored layers on their anchor; a layer whose anchor (or <details>) left the page (htmx swap) closes */
  function replace() {
    queued = false;
    for (var i = stack.length - 1; i >= 0; i--) {
      var L = stack[i], gone = L.details ? !d.contains(L.el) : !!L.anchor && !d.contains(L.anchor);
      if (gone) closeAt(i, false); else place(L);
    }
  }
  function later() { if (stack.length && !queued) { queued = true; window.requestAnimationFrame(replace); } }
  window.addEventListener('resize', later);
  d.addEventListener('scroll', later, { capture: true, passive: true });
  if (mq.addEventListener) mq.addEventListener('change', later);
})();
