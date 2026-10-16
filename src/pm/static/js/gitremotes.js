/* Remove gitdev needs a typed confirmation: ask in-page (pmPrompt), put the answer into the hidden
   typed-answer field and submit. The server checks it again. Other removals use the htmx confirm attribute. */
(function () {
  'use strict';
  document.addEventListener('click', function (e) {
    var b = e.target && e.target.closest && e.target.closest('button[data-remote-typed]');
    if (!b || !window.pmPrompt) return;
    e.preventDefault(); e.stopImmediatePropagation();
    var want = b.getAttribute('data-remote-typed'), f = b.form;
    window.pmPrompt({ title: 'Remove the ' + want + ' remote? Push and the commit links stop working.',
                      label: 'Type ' + want + ' to confirm', confirmLabel: 'Remove' }).then(function (v) {
      if (v == null || String(v).trim() !== want) return;
      var h = f.querySelector('input[name=confirm]');
      if (h) h.value = String(v).trim();
      if (f.requestSubmit) f.requestSubmit(); else f.submit();
    });
  }, true);
  /* Generate key (POST /settings/ssh/keygen via htmx, no navigation): reload the remotes panel to show the key. */
  document.addEventListener('htmx:afterRequest', function (e) {
    var f = e.detail && e.detail.elt;
    if (!f || !f.matches || !f.matches('form[data-gr-refresh]') || !window.htmx) return;
    window.htmx.ajax('GET', f.getAttribute('data-gr-refresh'), { target: '#git-remotes', swap: 'outerHTML' });
  });
})();
