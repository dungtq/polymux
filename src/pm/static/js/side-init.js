/* Before first paint: restore this tab's collapsed sidebar flag (sessionStorage: per tab, a new tab starts expanded) and the saved dragged
   width (localStorage, --side-user, used only >=900px) (CSP forbids inline script). */
(function () {
  function ld(k) { try { return sessionStorage.getItem(k); } catch (e) { return null; } }
  try { if (ld('pm.v5.side') === '1') document.documentElement.classList.add('side-collapsed'); } catch (e) { /* ignore */ }
  /* Project list: the server renders open (Projects / a project page) or collapsed (anywhere else); a manual toggle is kept in this tab
     for the same kind of page ("p:1" / "o:0") and applied here as html.pj-1 / pj-0 so nothing flashes; side.js takes it over. */
  try {
    var pv = ld('pm.v5.projopen'), here = /^\/p(\/|$)/.test(location.pathname) ? 'p' : 'o';
    if (pv && pv.charAt(0) === here && pv.charAt(1) === ':') document.documentElement.classList.add('pj-' + pv.charAt(2));
  } catch (e) { /* ignore */ }
  /* Inbox views (Active / Idle / Ended): the server renders open on the Inbox pages and folded elsewhere; a manual toggle is kept in
     this tab for the same kind of page ("i:1" / "o:0") and shown pre-paint as html.ib-1 / ib-0. */
  try {
    var iv = ld('pm.v5.inboxopen'), ih = location.pathname === '/' ? 'i' : 'o';
    if (iv && iv.charAt(0) === ih && iv.charAt(1) === ':') document.documentElement.classList.add('ib-' + iv.charAt(2));
  } catch (e) { /* ignore */ }
  try {
    var w = parseInt(localStorage.getItem('pm.v5.sidew'), 10), m = Math.floor(window.innerWidth * 0.4);
    if (w >= 180) document.documentElement.style.setProperty('--side-user', Math.min(w, Math.max(180, m)) + 'px');
  } catch (e) { /* ignore */ }
})();
