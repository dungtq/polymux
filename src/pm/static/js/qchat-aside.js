/* btw: pm's own /btw. A side question about the agent on this page goes to Quick chat (claude -p), never to
   the agent's tmux pane. POST /qchat/send gets `agent=<id>` on the first turn of the new conv (qchat.js run());
   the server adds that agent's recent chat as context and keeps the id on the conv (`conv.agent`).
   - pm.qchat.aside(id, label, question?): new conv bound to the agent, header chip "<agent id> ↗", sends `question`.
   - Composer: the btw icon ([data-aside]) and `/btw <question>` (capture-phase submit: htmx never posts it).
   - "Use in composer" under each answer of a btw conv: inserts the plain text at the agent composer's cursor, unsent
     (pm.composer.insert of that composer's own document; quick chat's own box is a composer too and never the target).
   Loaded on every page after qchat.js. In the drawer (embed: no quick chat of its own) the composer hands off to the
   top page's quick chat (same origin); without either, the btw button stays hidden and /btw goes to the agent. */
(function () {
  "use strict";
  var q = window.pm && window.pm.qchat, up = null;
  try { up = window.top !== window && window.top.pm && window.top.pm.qchat; } catch (e) { up = null; }
  if (!q) {                                          /* the drawer: hand off to the top page's quick chat */
    if (up && up.aside) composer(function (id, lbl, t) { return up.aside(id, lbl, t); });
    return;
  }
  var el = q.el, log = q.$(".qc-log"), label = "";

  /* ---- header chip: "<agent id> ↗", a link to the agent page ---- */
  var chip = el("a", "qc-aside"), chipT = el("span", "qc-asl");
  chip.hidden = true; chip.appendChild(chipT);        /* the link is the touch target, the span the pill */
  q.$(".qc-t").insertAdjacentElement("afterend", chip);
  if (window.top !== window) chip.target = "_top";
  /* the header drags / double-click maximises the popup: keep both off the link */
  chip.addEventListener("pointerdown", function (e) { e.stopPropagation(); });
  chip.addEventListener("dblclick", function (e) { e.stopPropagation(); });
  function drawChip(c) {
    var id = c && c.agent, box = document.getElementById("qchat");
    chip.hidden = !id;
    if (box) box.classList.toggle("qc-btw", !!id);       /* btw runs without tools: hide Tools / Folder / MCP / Skills */
    if (!id) return;
    var t = c.title && c.title.indexOf("btw: ") === 0 ? c.title.slice(5) : "";
    var name = label || t || id;
    chipT.textContent = "";                              /* the title already says "btw: <label>": the chip is the way back */
    chipT.appendChild(el("span", "qc-asid", id));        /* phones: hidden, only the arrow (qchat.css) */
    chipT.appendChild(document.createTextNode(" \u2197"));
    chip.setAttribute("aria-label", "Open agent " + id);
    chip.href = "/a/" + encodeURIComponent(id);
    chip.title = "btw about " + name + ": Quick chat with its recent conversation as context. Open the agent";
  }
  document.addEventListener("pm:qchat-conv", function (e) { label = ""; drawChip(e.detail); });

  function aside(id, lbl, question) {
    if (!id) return;
    var busy = !!q.ctl();
    q.open(true);
    return Promise.resolve(q.loadOpts()).then(function () {
      q.newConv();                                   /* fires pm:qchat-conv(null): chip hidden, label reset */
      q.setAgent(id); label = lbl || id; drawChip(q.conv());
      if (!question) return;
      q.inp.value = question;
      q.inp.dispatchEvent(new Event("input", { bubbles: true }));  /* Send follows the box (composer.js) */
      /* a stream that newConv() just aborted still settles; let the user press Enter instead of racing it */
      if (!busy) q.form.requestSubmit();
    });
  }
  q.aside = aside;

  /* ---- "Use in composer" under each finished answer of a btw conv ---- */
  /* the composer of the conv's agent: this page's, else one in a same-origin frame (the chat drawer) */
  var AGENT_FORM = '[data-composer="live"] [data-cmp-form], [data-composer="builder"] [data-cmp-form]';
  function box() {
    var c = q.conv(), want = c && c.agent ? "/a/" + c.agent + "/message" : "";
    var docs = [document];
    Array.prototype.forEach.call(document.querySelectorAll("iframe"), function (f) {
      try { if (f.contentDocument) docs.push(f.contentDocument); } catch (e) { /* other origin */ }
    });
    var any = null;
    for (var i = 0; i < docs.length; i++) {
      var f = docs[i].querySelector(AGENT_FORM), ta = f && f.querySelector("[data-cmp-input]");
      if (!ta) continue;
      if (!want || f.getAttribute("action") === want) return ta;
      any = any || ta;
    }
    return any;
  }
  function useIn(text) {
    var ta = box(), W = ta && (ta.ownerDocument.defaultView || window), cmp = W && W.pm && W.pm.composer;  /* the drawer frame: its own */
    if (!cmp || !cmp.insert) return;
    var r = cmp.insert(ta, text);                                 /* at the cursor; grow, draft, Send button state */
    if (!r.ok) { q.say("The composer is full (" + ta.maxLength + " characters)."); return; }
    /* the popup over the message box (phone, short laptop screens): close it so the inserted text shows */
    var pr = document.getElementById("qchat").getBoundingClientRect(), tr = ta.getBoundingClientRect();
    if (ta.ownerDocument === document && pr.bottom > tr.top && pr.top < tr.bottom && pr.right > tr.left && pr.left < tr.right) { q.open(false); ta.focus(); }
    q.say(r.cut ? "Added to the composer, cut at " + ta.maxLength + " characters. Not sent." : "Added to the composer. Not sent.", "ok");
  }
  function decorate(meta) {
    var c = q.conv(), ans = meta.previousElementSibling;
    if (!c || !c.agent || !box() || !ans || !ans.classList.contains("qc-a") || meta.querySelector(".qc-use")) return;
    var b = el("button", "qc-use", "Use in composer");
    b.type = "button";
    b.title = "Insert this answer into the agent's message box at the cursor (not sent)";
    b.addEventListener("click", function () { useIn((ans.innerText || ans.textContent || "").trim()); });
    meta.appendChild(b);
  }
  /* each finished answer gets its .qc-meta line (history and live `done`); the action sits at its end */
  new MutationObserver(function (recs) {
    recs.forEach(function (r) {
      Array.prototype.forEach.call(r.addedNodes, function (n) { if (n.nodeType === 1 && n.classList.contains("qc-meta")) decorate(n); });
    });
  }).observe(log, { childList: true });

  composer(aside);

  /* ---- composer: btw button and `/btw <question>` ---- */
  function composer(asideFn) {
    var BTW = /^\/btw(?:\s+([\s\S]*))?$/i;
    Array.prototype.forEach.call(document.querySelectorAll("[data-composer] [data-aside]"), function (b) { b.hidden = false; });
    function take(btn, ta, text) {
      asideFn(btn.getAttribute("data-agent"), btn.getAttribute("data-label"), text);
      if (text != null && ta) { ta.value = ""; ta.dispatchEvent(new Event("input", { bubbles: true })); }
    }
    document.addEventListener("click", function (e) {
      var b = e.target.closest && e.target.closest("[data-composer] [data-aside]");
      if (!b) return;
      e.preventDefault();
      var ta = b.closest("[data-composer]").querySelector("[data-cmp-input]"), t = ta ? ta.value.trim() : "";
      take(b, ta, t || null);                       /* the composer is cleared only when it handed off a question */
    });
    /* Capture on document: runs before htmx's own submit listener on the form (Enter via composer.js requestSubmit,
       the Send button, and slash.js htmx.trigger(form, "submit")), so a /btw message is never POSTed to the agent.
       A bare /btw opens an empty btw. */
    document.addEventListener("submit", function (e) {
      var f = e.target;
      if (!f || !f.matches || !f.matches("[data-composer] [data-cmp-form]")) return;
      var ta = f.querySelector("[data-cmp-input]"), m = ta && BTW.exec(ta.value.trim());
      var btn = f.closest("[data-composer]").querySelector("[data-aside]");
      if (!m || !btn) return;
      e.preventDefault(); e.stopImmediatePropagation();
      take(btn, ta, (m[1] || "").trim());
    }, true);
  }
})();
