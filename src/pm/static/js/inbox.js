/* Unread badge on the Inbox tab and the installed app icon (Badging API where supported). */
(function () {
  if (window.__pmInbox) return;
  window.__pmInbox = true;
  var last = -1, lastSys = -1;

  function applySystem(n) {  // bell + Servers badge: unread system messages (no agent)
    if (n === lastSys) return;
    lastSys = n;
    window.pm.badges.set(document.querySelectorAll("[data-system-badge]"), n);
  }
  function apply(n) {
    if (n === last) return;
    last = n;
    window.pm.badges.set(document.querySelectorAll("[data-inbox-badge]"), n);
    try {
      if (n > 0 && navigator.setAppBadge) navigator.setAppBadge(n).catch(function () {});
      else if (navigator.clearAppBadge) navigator.clearAppBadge().catch(function () {});
    } catch (e) { /* unsupported */ }
  }
  function refresh() {
    window.pm.fetch("/inbox/unread", { credentials: "same-origin", headers: { Accept: "application/json" } })
      .then(function (r) { return r.ok ? r.json() : null; })
      .then(function (d) {
        if (!d) return;
        if (typeof d.unread === "number") apply(d.unread);
        if (typeof d.system === "number") applySystem(d.system);
      })
      .catch(function () {});
  }
  // Opening a row marks it read implicitly (agent rows navigate; ntfy rows expand).
  function markRead(key) {
    if (!key) return;
    var body = new URLSearchParams({ csrf: window.pm.csrf(), key: key });
    try { window.pm.fetch("/inbox/read", { method: "POST", body: body, credentials: "same-origin", keepalive: true }).catch(function () {}); } catch (e) { /* ignore */ }
  }
  document.addEventListener("click", function (ev) {
    var bl = ev.target.closest && ev.target.closest("a[data-bell-read]");
    if (bl) { markRead(bl.getAttribute("data-bell-read")); bl.removeAttribute("data-bell-read"); return; }
    var a = ev.target.closest && ev.target.closest(".nwrap > a[data-read]");  // the shared agent row (Inbox, project page)
    if (a) { markRead(a.getAttribute("data-read")); a.removeAttribute("data-read"); a.classList.remove("unread"); return; }
    var sm = ev.target.closest && ev.target.closest(".ntfy-row[data-unread] > summary");
    if (sm && !ev.target.closest(".ntfy-pin")) { var d = sm.parentNode; markRead(d.getAttribute("data-key")); d.removeAttribute("data-unread"); }
  });
  window.pm.badges.on(refresh);  // agent / inbox events, tab shown again, every minute (core.js)
  document.addEventListener("DOMContentLoaded", refresh);
  if (document.readyState !== "loading") refresh();
})();
