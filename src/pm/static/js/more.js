/* Phone tab bar "More..." menu: a popup.js popover above the button, a bottom sheet under 700px (data-sheet). A second tap,
   an outside tap or Escape closes it (pm.popover). */
(function () {
  var b = document.querySelector(".tabmore"), s = document.getElementById("moresheet");
  if (!b || !s || !window.pm || !window.pm.popover) return;
  b.addEventListener("click", function () { window.pm.popover.toggle(b, s, { placement: "top-end", role: "menu" }); });
})();
