/* ntfy panel: keep expanded rows across HTMX swaps and the 15s refresh.
   Desktop and phone: a click on the agent id shows that agent's recap inside the row (same /a/<id>/recap-row as the
   agent tables, loaded by pm.loadRecap); a click anywhere else on a row that has an agent opens the full chat. */
(function () {
  if (window.__pmNtfy) return;
  window.__pmNtfy = true;
  var open = new Set(), recap = new Set();
  var sel = ".ntfy-row[data-id]";
  var bypass = false;

  function showRecap(d, on) {
    if (on) { d.setAttribute("data-recap-open", ""); d.open = true; var b = d.querySelector(".ntfy-recap"); if (b) window.pm.loadRecap(b); }
    else d.removeAttribute("data-recap-open");
    var ag = d.querySelector("a.ntfy-agent");
    if (ag) ag.setAttribute("aria-expanded", on ? "true" : "false");
  }
  function restore(root) {
    (root || document).querySelectorAll(sel).forEach(function (d) {
      if (open.has(d.dataset.id)) d.open = true;
      if (recap.has(d.dataset.id)) showRecap(d, true);
    });
  }
  document.addEventListener("toggle", function (e) {
    var d = e.target;
    if (!d.matches || !d.matches(sel)) return;
    if (d.open) open.add(d.dataset.id); else { open.delete(d.dataset.id); recap.delete(d.dataset.id); showRecap(d, false); }
  }, true);
  /* capture phase: before the drawer opens the chat aside from the agent link, and before <summary> toggles */
  function onAgent(e) {
    var sm = e.target.closest(".ntfy-row > summary"), ag = sm && sm.querySelector("a.ntfy-agent");
    if (!ag || bypass || e.target.closest(".ntfy-t a")) return;
    var d = sm.parentNode;
    e.preventDefault();
    if (e.target.closest("a.ntfy-agent")) {
      e.stopImmediatePropagation();
      var on = !d.hasAttribute("data-recap-open");
      if (on) recap.add(d.dataset.id); else recap.delete(d.dataset.id);
      showRecap(d, on);
      if (!on) d.open = false;
      return;
    }
    /* the rest of the row opens the full chat: a plain click on the agent link (drawer aside, else navigation) */
    bypass = true;
    try { ag.click(); } finally { bypass = false; }
  }
  document.addEventListener("click", function (e) { if (e.target.closest) onAgent(e); }, true);
  document.addEventListener("keydown", function (e) {
    if (e.key === " " && e.target.classList && e.target.classList.contains("ntfy-agent")) onAgent(e);
  }, true);
  document.addEventListener("htmx:afterSwap", function (e) { restore(e.target); });
  document.addEventListener("htmx:afterSettle", function () { restore(document); });
  document.addEventListener("nodiff:swapped", function (e) { restore(e.target); });
})();
