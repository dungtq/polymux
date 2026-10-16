/* Top-right dropdowns (<details id="acct-menu"> account menu, <details id="bell-menu"> system messages). Outside click, Escape,
   arrow keys, the phone bottom sheet and "opening one closes the other" are popup.js (pm.popover); this file closes a menu
   after an item is chosen and fetches the bell body each time it opens. */
(function () {
  var menus = ["acct-menu", "bell-menu"].map(function (id) { return document.getElementById(id); }).filter(Boolean);
  menus.forEach(function (d) {
    d.addEventListener("click", function (e) {
      if (e.target.closest && e.target.closest(".acct-it, .bell-row a, .bell-more, .ntfy-mark")) {
        setTimeout(function () { if (window.pm && window.pm.popover) window.pm.popover.close(d); else d.open = false; }, 0);
      }
    });
    d.addEventListener("toggle", function () {
      if (!d.open) return;
      var pop = d.querySelector("[hx-get]");
      if (pop && window.htmx) window.htmx.trigger(pop, "bell-open");
    });
  });
})();
