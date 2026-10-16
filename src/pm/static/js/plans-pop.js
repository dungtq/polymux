/* Plans / New plan on the project page open in a modal dialog holding the embedded plans page (?embed=1).
   pmDialog (dialog.js) gives it the backdrop, Esc and the [data-dlg-close] button. */
(function () {
  var dlg = document.getElementById('plans-dialog');
  if (!dlg || !window.pmDialog) return;
  var fr = dlg.querySelector('iframe');
  document.addEventListener('click', function (e) {
    var b = e.target.closest('[data-plans-pop]');
    if (!b) return;
    e.preventDefault();
    fr.src = b.getAttribute('data-plans-pop');
    window.pmDialog(dlg, { onClose: function () { fr.src = 'about:blank'; } });
  });
})();
