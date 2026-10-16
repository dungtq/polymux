// Full screen toggle for a rendered plan (CSS overlay; Esc leaves).
(function () {
  var btn = document.querySelector('[data-plan-full]'), doc = document.getElementById('plan-doc');
  if (!btn || !doc) return;
  function set(on) {
    doc.classList.toggle('is-full', on);
    document.documentElement.classList.toggle('plan-full', on);
    btn.setAttribute('aria-pressed', on ? 'true' : 'false');
    btn.textContent = on ? 'Leave full screen' : 'Full screen';
  }
  btn.addEventListener('click', function () { set(!doc.classList.contains('is-full')); });
  document.addEventListener('keydown', function (e) {
    if (e.key === 'Escape' && doc.classList.contains('is-full')) set(false);
  });
})();
