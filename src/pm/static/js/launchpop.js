/* Launch popup. A plain click on a "Launch" link (/p/<project>/launch) opens the launch form in a modal
   <dialog> (pmDialog frame dialog: iframe ?embed=1, backdrop, Esc, close x) instead of a new page; ctrl/cmd/shift/alt
   and middle click stay real links. The framed page (launch.js) posts {pm:'launchpop', act:'close'|'open'|'stay'} back. */
(function () {
  var d = document, LAUNCH = /^(?:\/p\/[^/]+)?\/launch$/, AGENT = /^\/a\/[a-z0-9]+(?:-[a-z0-9]+)*$/, dlg = null, frame = null, pe = null;
  function blank() { try { frame.contentWindow.location.replace('about:blank'); } catch (e) { frame.src = 'about:blank'; } }
  function close() { if (dlg && dlg.open) dlg.close(); }
  function open(href) {
    var pm = href.indexOf('from=inbox') > -1 ? null : /^\/p\/([^/]+)\//.exec(href), src = href + (href.indexOf('?') > -1 ? '&' : '?') + 'embed=1';
    if (!dlg) {  // built once and kept (keep: true), so the iframe is reused
      dlg = window.pmDialog({ title: 'Launch agent', label: 'Launch agent', iframe: src, closeX: true, cls: 'launchpop', keep: true, onClose: blank });
      frame = dlg.querySelector('iframe');
      pe = d.createElement('span'); pe.className = 'lp-proj';
      dlg.querySelector('.pmdlg-title').after(pe);
    } else {
      frame.src = src;
      if (!dlg.open) window.pmDialog(dlg, { keep: true, onClose: blank });
    }
    try { pe.textContent = pm ? '\u00b7 ' + decodeURIComponent(pm[1]) : ''; } catch (x) { pe.textContent = ''; }
  }
  d.addEventListener('click', function (e) {
    if (e.defaultPrevented || e.button || e.metaKey || e.ctrlKey || e.shiftKey || e.altKey) return;
    var a = e.target.closest('a[href]');
    if (!a || a.target === '_blank' || typeof HTMLDialogElement === 'undefined' || !window.pmDialog) return;
    var u; try { u = new URL(a.getAttribute('href'), location.href); } catch (x) { return; }
    if (u.origin !== location.origin || !LAUNCH.test(u.pathname) || (u.search && u.search !== '?from=inbox' && !/^\?project=[^&]+$/.test(u.search))) return;
    e.preventDefault(); open(u.pathname + u.search);
  });
  window.addEventListener('message', function (e) {
    var m = e.data;
    if (e.origin !== location.origin || !m || m.pm !== 'launchpop') return;
    if (m.act === 'close') close();
    else if (m.act === 'nav' && /^\/(p|a|term)(\/|$)/.test(m.href || '')) { close(); location.href = m.href; }
    else if (m.act === 'stay') {
      close();
      var live = d.getElementById('proj-live') || d.getElementById('inbox');
      if (live && window.htmx) window.htmx.trigger(live, 'pm:refresh'); else location.reload();
    } else if (m.act === 'open' && AGENT.test(m.href || '')) {
      close();
      if (d.getElementById('v5chat')) location.hash = '#chat=' + m.href; else location.href = m.href;
    }
  });
})();
