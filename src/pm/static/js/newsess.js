/* tmux "New session" box: Cancel closes the <details> popover (popup.js) and clears the field. */
document.addEventListener("click", function (ev) {
  var btn = ev.target.closest("[data-newsess-cancel]");
  if (!btn) return;
  var d = btn.closest("details");
  var f = btn.closest("form");
  if (f) f.reset();
  if (d && window.pm && window.pm.popover) window.pm.popover.close(d); else if (d) d.removeAttribute("open");
});
