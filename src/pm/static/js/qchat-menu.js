/* Quick chat popup, part 2: the history row menu (a pm.popover menu), header buttons and the global open / Esc keys,
   then the initial paint and `?qchat=<id>`. Loaded right after qchat.js (deferred); uses window.pm.qchat from there and
   does nothing when the page has no quick chat popup. Enter and the "/" skills menu belong to the composer
   (composer.js, slash.js); an open popover takes Esc first (popup.js), so Esc here only closes the panel. */
(function () {
  "use strict";
  var q = window.pm && window.pm.qchat;
  if (!q) return;
  var P = q.P, $ = q.$, menu = q.menu;
  var open = q.open, openConv = q.openConv, newConv = q.newConv, setTitle = q.setTitle, refreshList = q.refreshList;
  var post = q.post, fail = q.fail, say = q.say, frame = q.frame, menuId = 0;

  /* ---- row menu, rows, header buttons ---- */
  $(".qc-x").addEventListener("click", function () { open(false); });
  $(".qc-new").addEventListener("click", newConv);
  function byId(id) { var convs = q.convs(); for (var i = 0; i < convs.length; i++) if (convs[i].id === id) return convs[i]; return null; }
  menu.addEventListener("click", function (e) {
    var b = e.target.closest("[data-qc-act]"); if (!b) return;
    var act = b.dataset.qcAct, id = menuId, c = byId(id); window.pm.popover.close(menu);
    if (act === "rename") {
      window.pmPrompt({ title: "Rename conversation", label: "Title", value: c ? c.title : "", confirmLabel: "Rename" }).then(function (v) {
        if (v == null || !v.trim()) return;
        post("/qchat/convs/" + id + "/rename", { title: v.trim() }).then(function () {
          var C = q.conv();
          if (C && C.id === id) { C.title = v.trim(); setTitle(C.title); }
          refreshList();
        }).catch(function (er) { fail("Rename", er); });
      });
    } else if (act === "copy") {
      window.pm.fetch("/qchat/convs/" + id + "/export").then(function (r) { if (!r.ok) throw new Error(String(r.status)); return r.text(); })
        .then(function (t) { return window.pm.copy(t); })
        .then(function (ok) { if (!ok) throw new Error("clipboard refused"); say("Copied as Markdown.", "ok"); })
        .catch(function (er) { fail("Copy", er); });
    } else if (act === "delete") {
      window.pmConfirm({ title: "Delete this conversation?", body: c ? c.title : "", confirmLabel: "Delete", danger: true }).then(function (ok) {
        if (!ok) return;
        post("/qchat/convs/" + id + "/delete").then(function () {
          var C = q.conv();
          if (C && C.id === id) newConv();
          refreshList();
        }).catch(function (er) { fail("Delete", er); });
      });
    }
  });
  P.addEventListener("click", function (e) {
    var rx = e.target.closest(".qc-rx");
    if (rx) {  /* a second click on the same row's dots closes it; pm.popover places it and handles Esc / outside */
      e.preventDefault(); e.stopPropagation();
      var id = Number(rx.closest(".qc-row").dataset.id), pop = window.pm.popover, again = pop.isOpen(menu) && menuId === id;
      if (pop.isOpen(menu)) pop.close(menu);
      menuId = id;
      if (again) return;
      (rx.closest(".qc-hist") || P).appendChild(menu);  /* inside the dropdown: a tap on the menu keeps the dropdown open */
      pop.open(rx, menu, { placement: "bottom-end", role: "menu" });
      return;
    }
    var rw = e.target.closest(".qc-row");
    if (rw) { e.preventDefault(); openConv(Number(rw.dataset.id)); }
  });
  document.addEventListener("click", function (ev) {
    if (ev.target.closest && ev.target.closest("[data-qchat-open]")) { ev.preventDefault(); open(P.hidden); }
  });
  document.addEventListener("keydown", function (ev) {
    if (ev.key !== "Escape" || ev.defaultPrevented || P.hidden || document.querySelector("dialog[open]")) return;
    open(false);
  });
  window.__qchatFrame = frame; // test hook
  q.controls();
  q.maxed();

  /* ?qchat=<id> (navbar search hit): open that conversation, then drop the parameter */
  try {
    var u = new URL(location.href), qid = u.searchParams.get("qchat");
    if (qid && /^\d+$/.test(qid)) {
      u.searchParams.delete("qchat"); history.replaceState(null, "", u.pathname + (u.search || "") + u.hash);
      open(true); openConv(Number(qid));
    }
  } catch (e) {}
})();
