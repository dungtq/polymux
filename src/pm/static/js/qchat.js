/* Quick chat popup (v2). Data only through /qchat/* (options, convs, send, rename, delete, export).
   POST /qchat/send streams SSE read with fetch: `conv` {id,title} first, then delta/tool/toolres/done/error.
   Streamed text uses textContent; the final reply is the server-rendered (escaped) markdown. Message text is
   never logged. Opens small and centered; the header drags it, Expand makes it a full window with the history
   list on the left. Esc or x closes. `?qchat=<id>` on load opens that conversation (navbar search hit).
   The message box is the shared composer (templates/_composer_macros.html composer("qchat"), composer.js): send key,
   draft, autosize, Send <-> Stop (onStop aborts the answer), text files and clipboard text (composer-attach.js
   data-attach="inline"), the "/" skills menu (slash.js; data-slash = the ticked skills). This file reads its form's
   submit. The history dropdown and the MCP / Skills pickers are pm.popover layers. The row menu, global keys and
   initial paint live in qchat-menu.js (loaded right after). */
(function () {
  "use strict";
  var P = document.getElementById("qchat");
  if (!P) return;
  var $ = function (s) { return P.querySelector(s); };
  var log = $(".qc-log"), cmp = $("[data-composer]"), form = $("[data-cmp-form]"), inp = $("[data-cmp-input]"), model = $(".qc-model");
  var tsel = $("#qc-tools"), dsel = $("#qc-dir"), fold = $(".qc-fold"), note = $(".qc-note"), gone = $(".qc-gone");
  var hb = $("[data-qc-hist]"), hp = $(".qc-hist"), menu = $(".qc-rmenu"), mx = $(".qc-max");
  var mcpBox = $(".qc-mcps"), mcpb = mcpBox.querySelector(".qc-mcpbtn"), mcpp = mcpBox.querySelector(".qc-mcppop"), mcpn = mcpBox.querySelector(".qc-mcpn");
  var skBox = $(".qc-skl"), skb = skBox.querySelector(".qc-mcpbtn"), skp = skBox.querySelector(".qc-mcppop"), skn = skBox.querySelector(".qc-mcpn");
  var C = null, started = false, ctl = null, cur = null, tools = {}, opts = null, optsP = null;
  var convs = [], query = "", selMcp = [], selSk = [];
  var pm = window.pm;
  function pop() { return pm.popover; }
  if (pm.composer && pm.composer.init) pm.composer.init(cmp, { onStop: function () { if (ctl) ctl.abort(); } });
  function el(tag, cls, text) { var e = document.createElement(tag); if (cls) e.className = cls; if (text != null) e.textContent = text; return e; }
  function open(v) {
    P.hidden = !v;
    var b = document.querySelector("[data-qchat-open]");
    if (b) b.setAttribute("aria-expanded", v ? "true" : "false");
    if (v) { place(); if (!opts) loadOpts().then(function () { if (!C) applyConv(null); }); refreshList(); setTimeout(function () { inp.focus(); }, 0); }
    else closePops();
  }
  var KEY = "pm.qchat.v2", st = {};
  try { st = JSON.parse(localStorage.getItem(KEY) || "{}") || {}; } catch (e) { st = {}; }
  function save() { try { localStorage.setItem(KEY, JSON.stringify(st)); } catch (e) {} }
  function narrow() { return window.matchMedia("(max-width:559px)").matches; }
  function place() {
    if (narrow() || st.max || typeof st.x !== "number") { P.style.left = P.style.top = P.style.right = ""; P.classList.remove("qc-pos"); return; }
    P.classList.add("qc-pos");
    var w = P.offsetWidth || 440;
    P.style.left = Math.max(0, Math.min(st.x, window.innerWidth - w)) + "px";
    P.style.top = Math.max(0, Math.min(st.y, window.innerHeight - 40)) + "px";
    P.style.right = "auto";
  }
  var head = $(".qc-head");
  function maxed() {
    P.classList.toggle("qc-full", !!st.max);
    mx.textContent = st.max ? "Restore" : "Expand";
    mx.title = st.max ? "Back to the small popup" : "Expand to a full chat window";
    mx.setAttribute("aria-pressed", st.max ? "true" : "false");
    place(); histShow(false);
  }
  mx.addEventListener("click", function () { st.max = !st.max; save(); maxed(); });
  head.addEventListener("dblclick", function (ev) { if (!ev.target.closest("button,select")) mx.click(); });
  head.addEventListener("pointerdown", function (ev) {
    if (ev.button || narrow() || st.max || ev.target.closest("button,select,input")) return;
    var r = P.getBoundingClientRect(), dx = ev.clientX - r.left, dy = ev.clientY - r.top;
    head.setPointerCapture(ev.pointerId); P.classList.add("qc-drag"); ev.preventDefault();
    function mv(e) { st.x = e.clientX - dx; st.y = e.clientY - dy; place(); }
    function up() {
      head.removeEventListener("pointermove", mv); head.removeEventListener("pointerup", up);
      head.removeEventListener("pointercancel", up); P.classList.remove("qc-drag");
      st.x = parseFloat(P.style.left) || 0; st.y = parseFloat(P.style.top) || 0; save();
    }
    head.addEventListener("pointermove", mv); head.addEventListener("pointerup", up); head.addEventListener("pointercancel", up);
  });
  window.addEventListener("resize", function () { place(); if (st.max && window.innerWidth >= 900) histShow(false); });

  /* ---- network helpers ---- */
  function getJSON(url) {
    return pm.fetch(url, { headers: { Accept: "application/json" } }).then(function (r) {
      if (!r.ok) throw new Error(String(r.status));
      return r.json();
    });
  }
  function post(url, fields) {
    var b = new URLSearchParams();
    Object.keys(fields || {}).forEach(function (k) { b.append(k, fields[k]); });
    return pm.fetch(url, { method: "POST", body: b, csrf: "both" }).then(function (r) {
      if (!r.ok) throw new Error(String(r.status));
      return r;
    });
  }
  /* notices: the composer's own line above the box (an ok one hides by itself) */
  function say(t, kind) {
    var s = cmp.querySelector("[data-cmp-flash]");
    if (!t) { if (s) s.textContent = ""; return; }
    pm.notify(t, { kind: kind || "err", target: s });
  }
  function fail(what, e) { say(what + " failed" + (e && e.message ? " (" + e.message + ")" : "") + "."); }

  /* ---- options (models, profiles, folders, MCP, skills) ---- */
  function loadOpts() {
    if (optsP) return optsP;
    optsP = getJSON("/qchat/options").then(function (o) {
      opts = o || {};
      var ms = opts.models || [];
      model.textContent = "";
      var d = el("option", "", "default"); d.value = ""; model.appendChild(d);
      ms.forEach(function (m) {
        var v = typeof m === "string" ? m : (m.id || m.value || m.name || ""), t = typeof m === "string" ? m : (m.label || m.name || v);
        if (!v) return; var op = el("option", "", t); op.value = v; model.appendChild(op);
      });
      tsel.textContent = "";
      (opts.profiles && opts.profiles.length ? opts.profiles : ["none"]).forEach(function (n) { var op = el("option", "", n); op.value = n; tsel.appendChild(op); });
      dsel.textContent = "";
      (opts.folders || []).forEach(function (n) { var op = el("option", "", n); op.value = n; dsel.appendChild(op); });
      return opts;
    }).catch(function (e) { optsP = null; opts = null; fail("Loading options", e); });
    return optsP;
  }
  function ensureOpt(sel, v) {
    if (!v) return;
    for (var i = 0; i < sel.options.length; i++) if (sel.options[i].value === v) return;
    var op = el("option", "", v); op.value = v; sel.appendChild(op);
  }

  /* ---- MCP / skills pickers ---- */
  function picker(pop, items, chosen, kind) {
    pop.textContent = "";
    if (kind === "mcp") pop.appendChild(el("p", "", "Servers enabled in Settings > Quick chat"));
    else {
      var p = el("p", "", "Skills enabled in Settings > Quick chat. Claude loads a ticked skill when it fits, or when you type ");
      p.appendChild(el("code", "", "/name")); pop.appendChild(p);
      var f = el("input", "qc-sksearch"); f.type = "search"; f.placeholder = "Filter skills"; f.setAttribute("aria-label", "Filter skills");
      f.addEventListener("input", function () {
        var v = f.value.toLowerCase();
        pop.querySelectorAll("label").forEach(function (l) { l.hidden = l.textContent.toLowerCase().indexOf(v) < 0; });
      });
      pop.appendChild(f);
    }
    if (!items.length) pop.appendChild(el("p", "qc-pop-empty", kind === "mcp" ? "No MCP servers enabled." : "No skills enabled."));
    items.forEach(function (it) {
      var l = el("label"), c = el("input"); c.type = "checkbox"; c.value = it.name; c.checked = chosen.indexOf(it.name) >= 0;
      l.appendChild(c); l.appendChild(document.createTextNode(" "));
      l.appendChild(el("span", "qc-mn", it.name));
      l.appendChild(el("span", "qc-ms", kind === "mcp" ? (it.source || "") + (it.type ? " · " + it.type : "") : (it.source || "")));
      if (it.description) l.title = it.description;
      pop.appendChild(l);
    });
  }
  function checked(pop) { return Array.prototype.map.call(pop.querySelectorAll("input[type=checkbox]:checked"), function (c) { return c.value; }); }
  /* the composer's "/" menu (slash.js) lists the ticked skills, while the profile allows skills */
  function slashSync() {
    var d = {};
    ((opts && opts.skills) || []).forEach(function (s) { d[s.name] = s.description || ""; });
    cmp.setAttribute("data-slash", JSON.stringify(skillsOk() ? selSk.map(function (n) { return { cmd: "/" + n, desc: d[n] || "skill" }; }) : []));
  }
  function counts() { mcpn.textContent = checked(mcpp).length; skn.textContent = checked(skp).length; slashSync(); }
  function onTicks() { selMcp = checked(mcpp); selSk = checked(skp); counts(); }
  mcpp.addEventListener("change", onTicks); skp.addEventListener("change", onTicks);
  mcpb.addEventListener("click", function () { pop().toggle(mcpb, mcpp, { placement: "top-start" }); });
  skb.addEventListener("click", function () {
    if (pop().toggle(skb, skp, { placement: "top-start" })) { var f = skp.querySelector(".qc-sksearch"); if (f) f.focus(); }
  });
  function shut(el) { if (pm.popover && !el.hidden) pm.popover.close(el); }
  function closePops() { [menu, mcpp, skp, hp].forEach(shut); }

  /* ---- current conversation -> controls ---- */
  var NOTE = {
    none: "One-shot <code>claude -p</code>, no tools: it cannot read files or run commands. Pasted or dropped text files are added to the message.",
    read: "<b>Read tools</b> in <code>~/dev/{dir}</code>: Read, Grep, Glob, WebFetch, WebSearch and the ticked MCP servers. It cannot write files or run commands.",
    full: "<b>Full tools</b> in <code>~/dev/{dir}</code>: Claude runs commands and edits files <b>without asking</b>. The audit records tool names only."
  };
  function profile() { return tsel.value || "none"; }
  function skillsOk() { var p = profile(); return p !== "none" || !!(opts && opts.skills_in_none); }
  function controls() {
    var p = profile();
    tsel.classList.toggle("qc-tools-full", p === "full");
    fold.hidden = p === "none";
    dsel.disabled = started;
    tsel.title = started ? "Tools profile (applies from the next message)" : "Tools profile for this conversation";
    mcpb.disabled = p === "none";
    mcpb.title = p === "none" ? "MCP needs the read or full tools profile" : "MCP servers for this conversation";
    skb.disabled = !skillsOk();
    skb.title = skillsOk() ? "Skills for this conversation (type / in the message to use one)" : "Skills need the read or full tools profile";
    if (mcpb.disabled) shut(mcpp);
    if (skb.disabled) shut(skp);
    slashSync();
    note.innerHTML = (NOTE[p] || NOTE.none).replace(/\{dir\}/g, pm.esc(dsel.value || "project"));
    note.classList.toggle("qc-warn", p === "full");
  }
  function setTitle(t) {
    t = t || "New conversation";
    $(".qc-tt").textContent = t; $(".qc-t").title = t;
  }
  function applyConv(c) { /* c = Conv from the server, or null for a fresh one */
    var o = opts || {}, on = function (l) { return (l || []).filter(function (x) { return x.on; }).map(function (x) { return x.name; }); };
    C = c; started = !!(c && c.msgs > 0);
    if (c) c.agent = c.agent || ""; /* Aside: the agent whose chat is the context (qchat-aside.js) */
    setTitle(c ? c.title : "");
    if (c) {
      ensureOpt(model, c.model || ""); model.value = c.model || ""; ensureOpt(tsel, c.profile || "none"); tsel.value = c.profile || "none";
      ensureOpt(dsel, c.folder); if (c.folder) dsel.value = c.folder;
      selMcp = (c.mcp || []).slice(); selSk = (c.skills || []).slice();
    } else {
      model.value = ""; tsel.value = o.default_profile && Array.prototype.some.call(tsel.options, function (x) { return x.value === o.default_profile; }) ? o.default_profile : tsel.options.length ? tsel.options[0].value : "none";
      if (o.default_folder) dsel.value = o.default_folder;
      selMcp = on(o.mcp); selSk = on(o.skills);
    }
    picker(mcpp, o.mcp || [], selMcp, "mcp"); picker(skp, o.skills || [], selSk, "skills"); counts();
    gone.hidden = !(c && c.archived);
    controls(); renderRows();
    document.dispatchEvent(new CustomEvent("pm:qchat-conv", { detail: C })); /* qchat-aside.js redraws its chip */
  }
  tsel.addEventListener("change", controls); dsel.addEventListener("change", controls);

  /* ---- history lists ---- */
  function dayStart(d) { return new Date(d.getFullYear(), d.getMonth(), d.getDate()).getTime() / 1000; }
  function group(ts) {
    var t0 = dayStart(new Date());
    return ts >= t0 ? "Today" : ts >= t0 - 86400 ? "Yesterday" : ts >= t0 - 6 * 86400 ? "Last 7 days" : "Older";
  }
  var MON = ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"];
  function p2(n) { return (n < 10 ? "0" : "") + n; }
  function hhmm(ts) { var d = new Date(ts * 1000); return p2(d.getHours()) + ":" + p2(d.getMinutes()); }
  function age(ts) {
    var g = group(ts), d = new Date(ts * 1000);
    return g === "Today" || g === "Yesterday" ? hhmm(ts) : MON[d.getMonth()] + " " + p2(d.getDate());
  }
  var DOTS = '<svg viewBox="0 0 24 24" width="14" height="14" fill="currentColor" aria-hidden="true" focusable="false"><circle cx="5" cy="12" r="1.8"/><circle cx="12" cy="12" r="1.8"/><circle cx="19" cy="12" r="1.8"/></svg>';
  function renderRows() {
    var frag = [], g = "";
    convs.forEach(function (c) {
      var gg = group(c.updated);
      if (gg !== g) { g = gg; var h = el("p", "qc-grp", g); frag.push(h); }
      var a = el("a", "qc-row"); a.href = "/?qchat=" + c.id; a.dataset.id = c.id;
      if (C && C.id === c.id) a.setAttribute("aria-current", "true");
      a.title = c.title + "\n" + (c.model || "default") + " · tools " + c.profile + (c.folder ? " · ~/dev/" + c.folder : "") + (c.archived ? " · session file gone (read-only)" : "");
      a.appendChild(el("span", "qc-rt", c.title));
      if (c.profile && c.profile !== "none") a.appendChild(el("span", "qc-tag t-" + (/^[a-z]+$/.test(c.profile) ? c.profile : "read"), c.profile));
      if (c.archived) { var t = el("span", "qc-tag t-old", "archived"); t.title = "Read-only: session file gone"; a.appendChild(t); }
      a.appendChild(el("span", "qc-rm", age(c.updated)));
      var x = el("button", "qc-rx"); x.type = "button"; x.setAttribute("aria-label", "More for this conversation"); x.title = "Rename, copy, delete";
      x.setAttribute("aria-haspopup", "menu"); x.setAttribute("aria-expanded", "false"); x.innerHTML = DOTS; a.appendChild(x);
      frag.push(a);
    });
    if (!convs.length) frag.push(el("p", "qc-pop-empty", query ? "No matches." : "No conversations yet."));
    P.querySelectorAll("[data-qc-list]").forEach(function (l) { l.textContent = ""; frag.forEach(function (n) { l.appendChild(n.cloneNode(true)); }); });
    /* clones carry no listeners: rows use delegation on P */
  }
  function refreshList() {
    return getJSON("/qchat/convs" + (query ? "?q=" + encodeURIComponent(query) : "")).then(function (d) { convs = (d && d.convs) || []; renderRows(); })
      .catch(function (e) { fail("Loading history", e); });
  }
  var qt = 0;
  P.querySelectorAll(".qc-hsearch").forEach(function (s) {
    s.addEventListener("input", function () {
      query = s.value.trim();
      P.querySelectorAll(".qc-hsearch").forEach(function (o) { if (o !== s) o.value = s.value; });
      clearTimeout(qt); qt = setTimeout(refreshList, 200);
    });
  });
  /* the history dropdown (the expanded window on a wide screen shows the side list instead) */
  function histShow(on) {
    if (on && !(st.max && window.innerWidth >= 900)) { pop().open(hb, hp, { placement: "bottom-start" }); refreshList(); }
    else shut(hp);
  }
  hb.addEventListener("click", function () { histShow(hp.hidden); });
  $("[data-qc-expand]").addEventListener("click", function (e) { e.preventDefault(); st.max = true; save(); maxed(); });

  /* ---- messages ---- */
  var toolN = 0;
  function toolRow(o) {
    var e = log.querySelector(".qc-empty"); if (e) e.remove();
    var d = document.createElement("details"); d.className = "qc-step";
    var s = document.createElement("summary");
    var c = document.createElement("span"); c.className = "tchip tf-" + (/^[a-z]+$/.test(o.fam || "") ? o.fam : "other");
    c.textContent = o.name === "Agent" ? "Sub-Agent" : o.name;
    var l = document.createElement("span"); l.className = "st-l"; l.textContent = o.line || "";
    s.appendChild(c); s.appendChild(l); d.appendChild(s);
    log.appendChild(d); log.scrollTop = log.scrollHeight; tools[o.id] = d; toolN++;
  }
  function toolRes(o) {
    var d = tools[o.id]; if (!d) return;
    var pre = document.createElement("pre"); pre.className = "qc-res" + (o.err ? " qc-err" : "");
    pre.textContent = o.text || "(no output)"; d.appendChild(pre);
    if (o.err) d.classList.add("qc-bad");
  }
  function add(cls, text) {
    var e = log.querySelector(".qc-empty"); if (e) e.remove();
    var d = document.createElement("div"); d.className = "qc-m " + cls; d.textContent = text;
    log.appendChild(d); log.scrollTop = log.scrollHeight; return d;
  }
  function meta(ts, mdl, n, cost) {
    var parts = [hhmm(ts), mdl || "default"];
    if (n) parts.push(n + (n === 1 ? " tool" : " tools"));
    if (typeof cost === "number") parts.push("$" + (cost < 0.1 ? cost.toFixed(3) : cost.toFixed(2)));
    var p = el("p", "qc-meta", parts.join(" · ")); p.title = new Date(ts * 1000).toLocaleString();
    log.appendChild(p); log.scrollTop = log.scrollHeight;
  }
  function showMsgs(msgs) {
    log.textContent = ""; tools = {};
    if (!msgs.length) { log.appendChild(el("p", "qc-empty muted small", "Ask anything. This conversation is saved to history after the first answer.")); return; }
    msgs.forEach(function (m) {
      if (m.role === "user") { add("qc-u", m.text); return; }
      toolN = 0; (m.tools || []).forEach(function (t) { toolRow(t); toolRes(t); });
      var d = el("div", "qc-m qc-a");
      if (m.html) { d.className = "qc-m qc-a md chat-text"; d.innerHTML = m.html; } else d.textContent = m.text || ""; // html is server-rendered, escaped markdown
      log.appendChild(d); meta(m.ts, m.model, (m.tools || []).length, m.cost);
    });
    log.scrollTop = log.scrollHeight;
  }
  function openConv(id) {
    if (ctl) ctl.abort();
    say(""); closePops();
    return loadOpts().then(function () { return getJSON("/qchat/convs/" + encodeURIComponent(id)); }).then(function (d) {
      applyConv(d.conv); showMsgs(d.messages || []);
    }).catch(function (e) { fail("Opening the conversation", e); });
  }
  function newConv() {
    if (ctl) ctl.abort();
    say(""); closePops();
    showMsgs([]); applyConv(null); inp.focus();
  }
  /* while the answer streams the box is off and Send is Stop (composer.js: running + empty + onStop) */
  function busy(v) { inp.disabled = v; if (pm.composer) pm.composer.running(cmp, v); if (!v) inp.focus(); }
  function frame(block) {
    var ev = "message", data = "";
    block.split("\n").forEach(function (l) {
      if (l.indexOf("event:") === 0) ev = l.slice(6).trim();
      else if (l.indexOf("data:") === 0) data += l.slice(5).trim();
    });
    var o = {}; try { o = JSON.parse(data); } catch (e) { return; }
    if (ev === "conv") {
      started = true; dsel.disabled = true;
      C = Object.assign(C || {}, { id: o.id, title: o.title, msgs: 1, profile: profile() }); setTitle(o.title);
      refreshList();
    }
    else if (ev === "delta") { if (!cur) cur = add("qc-a", ""); cur.textContent += o.t; log.scrollTop = log.scrollHeight; }
    else if (ev === "tool") { if (cur && !cur.textContent) cur.remove(); cur = null; toolRow(o); }
    else if (ev === "toolres") toolRes(o);
    else if (ev === "done") {
      if (o.session && C) C.session = o.session;
      if (cur && o.html) { cur.className = "qc-m qc-a md chat-text"; cur.innerHTML = o.html; log.scrollTop = log.scrollHeight; } // server-rendered, escaped markdown
      if (cur) { meta(Date.now() / 1000, model.value, typeof o.tools_n === "number" ? o.tools_n : toolN, o.cost); cur = null; }
      refreshList();
    }
    else if (ev === "error") add("qc-err", o.msg || "error");
  }
  function run(text) {
    add("qc-u", text); cur = add("qc-a", ""); tools = {}; toolN = 0; busy(true);
    ctl = new AbortController();
    var body = new URLSearchParams({ text: text, model: model.value, conv: C && C.id ? String(C.id) : "", profile: profile(), folder: profile() === "none" ? "" : dsel.value });
    if (profile() !== "none") selMcp.forEach(function (n) { body.append("mcp", n); });
    if (skillsOk()) selSk.forEach(function (n) { body.append("skills", n); });
    if (C && C.agent && !C.id) body.append("agent", C.agent); /* first turn of an Aside conv */
    pm.fetch("/qchat/send", { method: "POST", body: body, csrf: "both", signal: ctl.signal })
      .then(function (r) {
        if (!r.ok || !r.body) { add("qc-err", r.status === 403 ? "Not allowed (reload the page)." : r.status === 401 ? "Logged out." : "Failed (" + r.status + ")."); return; }
        var rd = r.body.getReader(), dec = new TextDecoder(), buf = "";
        function pump() {
          return rd.read().then(function (x) {
            if (x.done) return;
            buf += dec.decode(x.value, { stream: true });
            var i; while ((i = buf.indexOf("\n\n")) >= 0) { frame(buf.slice(0, i)); buf = buf.slice(i + 2); }
            return pump();
          });
        }
        return pump();
      })
      .catch(function (e) { if (e && e.name !== "AbortError") add("qc-err", "Connection lost."); })
      .then(function () { if (cur && !cur.textContent) cur.remove(); cur = null; ctl = null; busy(false); controls(); });
  }
  /* the composer sends through the form (send key, Send button, btw); the box clears like a sent message */
  form.addEventListener("submit", function (ev) {
    ev.preventDefault();
    var t = inp.value.trim(); if (!t || ctl) return;
    inp.value = ""; inp.dispatchEvent(new Event("input", { bubbles: true })); say(""); run(t);
  });

  /* shared with qchat-menu.js (loaded right after this file): DOM refs, helpers, getters for the mutable state */
  pm.qchat = {
    P: P, $: $, el: el, inp: inp, form: form, menu: menu,
    open: open, openConv: openConv, newConv: newConv, setTitle: setTitle, refreshList: refreshList,
    post: post, fail: fail, say: say, frame: frame, controls: controls, maxed: maxed,
    conv: function () { return C; }, convs: function () { return convs; }, ctl: function () { return ctl; },
    loadOpts: loadOpts, setAgent: function (a) { C = Object.assign(C || {}, { agent: a || "" }); }
  };
})();
