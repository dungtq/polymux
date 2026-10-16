/* Remember which per-project backlog <details> are open; Expand/Collapse all. */
(function () {
  var saved = window.pm.openset("pm.backlog.open");
  var groups = document.querySelectorAll("details.bl-group");
  groups.forEach(function (d) {
    var n = d.dataset.project;
    if (saved.has(n) || location.hash === "#bl-" + n) d.open = true;
    d.addEventListener("toggle", function () {
      saved.set(n, d.open);
    });
  });
  document.querySelectorAll(".bl-expand").forEach(function (b) {
    b.addEventListener("click", function () {
      var o = b.dataset.open === "1";
      groups.forEach(function (d) { d.open = o; });
    });
  });
})();

/* Project page: after a quick-add (redirect to ?added=1#backlog) flash "Added", highlight and
 * scroll to the new item (the last Inbox item), then drop the query so a reload is quiet. */
(function () {
  if (!/(^|[?&])added=1(&|$)/.test(location.search)) return;
  var items = document.querySelectorAll("#backlog ~ .bl-card .bl-item[data-inbox]");
  var last = items.length ? items[items.length - 1] : null;
  var flash = document.getElementById("bl-flash");
  if (flash) flash.hidden = false;
  if (last) {
    last.classList.add("bl-new");
    if (last.scrollIntoView) last.scrollIntoView({ block: "center" });
  }
  setTimeout(function () { if (flash) flash.hidden = true; }, 4000);
  try { history.replaceState(null, "", location.pathname + "#backlog"); } catch (e) { /* ignore */ }
})();

/* Edit view: hold an edit lease while the page is open (acquire, renew every 60 s and when the
 * tab becomes visible again, release on leave). Server enforces it; this only keeps it alive. */
(function () {
  var form = document.getElementById("bl-edit");
  if (!form || form.hasAttribute("data-readonly")) return;
  var csrf = window.pm.csrf();
  var leaseInput = form.querySelector('input[name="lease"]');
  var msg = document.getElementById("bl-lease-msg");
  var project = form.dataset.project;
  var left = false;
  function say(t) { if (msg) { msg.textContent = t; msg.hidden = !t; } }
  function post(url, extra) {
    var b = new URLSearchParams({ csrf: csrf, project: project, lease: leaseInput.value });
    return window.pm.fetch(url, { method: "POST", credentials: "same-origin", cache: "no-store",
      headers: { "Content-Type": "application/x-www-form-urlencoded" }, body: b.toString() });
  }
  function keep(id) {
    leaseInput.value = id;
    try {
      var u = new URL(location.href); u.searchParams.set("lease", id);
      history.replaceState(null, "", u.pathname + u.search + u.hash);
    } catch (e) { /* ignore */ }
  }
  function acquire() {
    return post("/backlog/lease/acquire").then(function (r) {
      if (r.status === 409) { location.reload(); return; }
      if (!r.ok) { say("Could not take the edit lease (" + r.status + ")."); return; }
      return r.json().then(function (j) { keep(j.lease); say(""); });
    }).catch(function () { say("Could not reach the server for the edit lease."); });
  }
  function renew() {
    if (left || !leaseInput.value) return acquire();
    return post("/backlog/lease/renew").then(function (r) {
      if (r.ok) { say(""); return; }
      if (r.status === 409) return acquire().then(function () {});
      say("Lease renew failed (" + r.status + ").");
    }).catch(function () { say("Could not reach the server to renew the edit lease."); });
  }
  function release() {
    if (left || !leaseInput.value) return;
    left = true;
    var b = new URLSearchParams({ csrf: csrf, project: project, lease: leaseInput.value });
    try { navigator.sendBeacon("/backlog/lease/release", b); } catch (e) { /* ignore */ }
  }
  acquire();
  setInterval(function () { if (!document.hidden) renew(); }, 60000);
  document.addEventListener("visibilitychange", function () { if (!document.hidden) renew(); });
  window.addEventListener("pagehide", function (e) { if (!e.persisted) release(); });
  window.addEventListener("pageshow", function (e) { if (e.persisted) { left = false; renew(); } });
  form.addEventListener("submit", function () { left = true; });
})();

/* Tick a backlog/TODO item in place: POST with fetch and swap only the list from the page the
 * redirect lands on. A full reload would drop the open chat drawer (its iframe and #chat= hash).
 * On any failure fall back to the plain form POST. */
(function () {
  function holder(form) {
    return form.closest("#todo-card, #backlog-card, details.bl-group");
  }
  /* quick-add on the project page: after the swap flash "Added" and highlight the new (last Inbox) item */
  function added(box) {
    var items = box.querySelectorAll(".bl-item[data-inbox]"), last = items.length ? items[items.length - 1] : null;
    var flash = document.getElementById("bl-flash");
    if (flash) { flash.hidden = false; setTimeout(function () { flash.hidden = true; }, 4000); }
    if (last) {
      last.classList.add("bl-new");
      if (last.scrollIntoView) last.scrollIntoView({ block: "nearest" });
    }
  }
  function swap(old, doc) {
    var neu = old.id ? doc.getElementById(old.id) : null;
    if (!neu) return false;
    if (old.nodeName === "DETAILS") {  /* keep the element (its toggle listener and open state) */
      old.innerHTML = neu.innerHTML;
      return true;
    }
    var more = old.querySelector("details.td-more");
    var wasOpen = more && more.open;
    old.innerHTML = neu.innerHTML;
    doc.querySelectorAll(".bl-seg [data-bl-view]").forEach(function (nb) {  /* Open/Done counts live in the pane head */
      var ob = document.querySelector('.bl-seg [data-bl-view="' + nb.dataset.blView + '"] .bl-seg-n'), nn = nb.querySelector(".bl-seg-n");
      if (ob && nn) ob.textContent = nn.textContent;
    });
    ["pj-p-backlog", "pj-p-todo"].forEach(function (pane) {  /* project tab pills */
      var sel = '.pj-tab[data-pj-pane="' + pane + '"] .pj-n', op = document.querySelector(sel), np = doc.querySelector(sel);
      if (op && np) op.textContent = np.textContent;
    });
    more = old.querySelector("details.td-more");
    if (more && wasOpen) more.open = true;
    return true;
  }
  document.addEventListener("submit", function (e) {
    var form = e.target;
    if (e.defaultPrevented || !form.matches || !form.matches("form.bl-item, #backlog-card form.bl-add")) return;
    var box = holder(form), adding = form.classList.contains("bl-add");
    if (!box || !window.fetch || !window.DOMParser) return;
    e.preventDefault();
    if (form.dataset.busy) return;
    form.dataset.busy = "1";
    var btn = form.querySelector(".bl-box, button[type=submit]");
    if (btn) btn.disabled = true;
    var body = new URLSearchParams(new FormData(form));
    window.pm.fetch(form.action, { method: "POST", credentials: "same-origin", cache: "no-store",
      headers: { "Content-Type": "application/x-www-form-urlencoded" }, body: body.toString() })
      .then(function (r) {
        if (!r.ok) throw new Error("http " + r.status);
        return r.text();
      })
      .then(function (html) {
        var doc = new DOMParser().parseFromString(html, "text/html");
        if (!swap(box, doc)) throw new Error("no list");
        if (adding) added(document.getElementById(box.id));
      })
      .catch(function () {
        delete form.dataset.busy;
        if (btn) btn.disabled = false;
        HTMLFormElement.prototype.submit.call(form);
      });
  });
})();

/* Edit an item: modal dialog, then POST /backlog/edit with the old text as the
 * guard (the server finds the line again under the lock, or answers 409 if the file changed). */
(function () {
  document.addEventListener("click", function (ev) {
    var b = ev.target.closest && ev.target.closest("[data-bl-edit]");
    if (!b) return;
    ev.preventDefault();
    var f = b.closest("form.bl-item");
    var old = f && f.querySelector('input[name="text"]');
    if (!f || !old || !window.pmPrompt) return;
    window.pmPrompt({ title: "Edit backlog item", label: "Text", value: old.value, confirmLabel: "Save" }).then(function (v) {
      if (v === null) return;
      v = v.replace(/\s+/g, " ").trim();
      if (!v || v === old.value) return;
      var n = document.createElement("input");
      n.type = "hidden"; n.name = "new_text"; n.value = v; f.appendChild(n);
      f.action = "/backlog/edit";
      /* requestSubmit fires the list's submit handler (fetch + swap) so the open chat drawer survives;
       * without it (old browser) fall back to a full navigation */
      if (f.requestSubmit) f.requestSubmit(); else f.submit();
    });
  });
})();

/* Framed editor (project page, Backlog tab, ?embed=1): Cancel and a finished save tell the parent page to close it. */
(function () {
  if (window.parent === window) return;
  function tell(saved) { try { window.parent.postMessage({ pm: "backlog-edit-close", saved: saved }, location.origin); } catch (e) { /* ignore */ } }
  if (document.querySelector("[data-bl-done]")) tell(true);
  document.addEventListener("click", function (e) {
    if (!e.target.closest("[data-bl-close]")) return;
    /* free the edit lease first (a beacon on unload can lose the race with the parent reopening the editor) */
    var f = document.getElementById("bl-edit"), v = function (n) { var i = f && f.elements[n]; return i ? i.value : ""; };
    if (!v("lease")) { tell(false); return; }
    var b = new URLSearchParams({ csrf: window.pm.csrf(), project: v("project"), lease: v("lease") });
    window.pm.fetch("/backlog/lease/release", { method: "POST", credentials: "same-origin", body: b, keepalive: true })
      .then(function () { tell(false); }, function () { tell(false); });
  });
})();

/* Project page, Backlog tab: Open / Done filter. The choice lives on #backlog-card (data-bl-view), which
 * the in-place tick/add swap keeps (it only replaces the card's children); CSS hides the other state. */
(function () {
  document.addEventListener("click", function (e) {
    var b = e.target.closest && e.target.closest("[data-bl-view]");
    var card = document.getElementById("backlog-card");
    if (!b || !card || !b.closest(".bl-seg")) return;
    card.setAttribute("data-bl-view", b.dataset.blView);
    b.closest(".bl-seg").querySelectorAll("[data-bl-view]").forEach(function (x) {
      x.setAttribute("aria-pressed", x === b ? "true" : "false");
    });
  });
})();
