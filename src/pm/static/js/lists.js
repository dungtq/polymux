// Expand all / Collapse all for <details class="sessrow"> on /tmux. No state is remembered.
document.addEventListener("click", function (ev) {
  var btn = ev.target.closest("[data-details-toggle]");
  if (!btn) return;
  var open = btn.getAttribute("data-details-toggle") === "open";
  document.querySelectorAll("details.sessrow").forEach(function (d) { d.open = open; });
});

// Filter / Show dropdowns: navigate on change (no-JS fallback is the form's submit button). The form's q field is the
// shared filter box (filter.js), so the current filter text rides along.
document.addEventListener("change", function (ev) {
  var sel = ev.target.closest && ev.target.closest("select[data-nav]");
  if (!sel || !sel.form) return;
  var form = sel.form;
  var p = new URLSearchParams(new FormData(form));
  Array.from(p.keys()).forEach(function (k) { if (!p.get(k)) p.delete(k); });
  var s = p.toString();
  window.location.href = (form.getAttribute("action") || "/") + (s ? "?" + s : "");
});

// /tmux?open=sN: the server opened that session row; bring it into view.
(function () {
  var d = document.querySelector("details.sessrow[data-focus]");
  if (d) d.scrollIntoView({ block: "start" });
})();
