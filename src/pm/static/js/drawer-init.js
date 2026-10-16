/* Before first paint: if the URL hash names a drawer (#chat=/a/ID, #term=sN or #plan=/p/X/plans/Y), open the layout at once so
   drawer.js (deferred) fills it without a flash of the closed page. Same regexes as drawer.js. Loaded at the
   top of <body> (CSP forbids inline script). */
(function () {
  try { var f = parseFloat(localStorage.getItem('pm.v5.chatf')); if (f >= 0.2 && f <= 0.8) document.documentElement.style.setProperty('--chat-f', String(f)); } catch (e) { /* ignore */ }
  var RX = /^#(chat|term|plan)=(.+)$/, m = RX.exec(location.hash), v, c = null, CK = 'pm.v5.carry';
  /* the previous page was left with its aside open (any link, form, select, filter or redirect; see drawer.js pagehide):
     carry it over. Fresh for 10 s; a hash in the URL always wins and clears the note. */
  try { c = JSON.parse(sessionStorage.getItem(CK) || 'null'); sessionStorage.removeItem(CK); } catch (e) { c = null; }
  if (!m && c && Date.now() - c.t < 10000 && RX.test(c.h || '')) {
    window.__pmCarry = { anchor: location.hash };
    try { history.replaceState(null, '', location.pathname + location.search + c.h); } catch (e) { /* ignore */ }
    m = RX.exec(location.hash);
  }
  if (!m) return;
  try { v = decodeURIComponent(m[2]); } catch (e) { return; }
  if (m[1] === 'chat' ? /^\/a\/[a-z0-9]+(?:-[a-z0-9]+)*$/.test(v)
      : m[1] === 'plan' ? /^\/p\/[^/?#]+\/plans\/[^/?#]+(?:\?src=[a-z]+)?$/.test(v) : /^s\d+$/.test(v)) document.body.classList.add('chat-open');
})();
