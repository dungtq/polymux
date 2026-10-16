/* pm project only: after an agent response (pm:agent_state, debounced) and on load, ask
 * GET /pm/reload-need whether this page needs a browser refresh (static files changed / service already
 * restarted). A needed service restart is the page bar #pmcode (reload.js, pm_code event), not this row.
 * Read-only polling by event. */
(function () {
  'use strict';
  var box = document.querySelector('[data-cmp-reload]');  // the composer's notice row
  if (!box || !window.fetch) return;
  var t = box.querySelector('.pmr-t'), rf = box.querySelector('.pmr-refresh');
  var served = box.getAttribute('data-served') || '0', timer = 0, busy = false;
  function show(j) {
    if (box.hasAttribute('data-lock')) return;  // reload.js owns the row while restarting / waiting
    if (!j || j.need !== 'refresh') { box.hidden = true; return; }
    t.textContent = j.msg;
    box.setAttribute('data-need', j.need);
    rf.hidden = false;
    box.hidden = false;
  }
  function check() {
    if (busy) return; busy = true;
    window.pm.fetch('/pm/reload-need?served=' + encodeURIComponent(served), { cache: 'no-store', credentials: 'same-origin' })
      .then(function (r) { return r.ok ? r.json() : null; })
      .then(show, function () { /* offline: keep what is shown */ })
      .then(function () { busy = false; });
  }
  function later() { clearTimeout(timer); timer = setTimeout(check, 1500); }
  rf.addEventListener('click', function () { location.reload(); });
  document.addEventListener('pm:agent_state', later);
  check();
})();
