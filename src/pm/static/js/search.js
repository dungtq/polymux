/* Global navbar search: "/" focuses, arrows move, Enter opens, Esc closes. The list is the /search fragment (htmx). */
(function () {
  var box = document.getElementById("gsearch");
  if (!box) return;
  var input = box.querySelector(".gs-in"), list = box.querySelector(".gs-list"), tgl = box.querySelector(".gs-tgl");
  var idx = -1, timer = null;
  function hits() { return Array.prototype.slice.call(list.querySelectorAll(".gs-hit")); }
  function setIdx(i) {
    var h = hits();
    h.forEach(function (a) { a.classList.remove("on"); a.removeAttribute("aria-selected"); });
    idx = h.length ? (i + h.length) % h.length : -1;
    if (idx >= 0) { h[idx].classList.add("on"); h[idx].setAttribute("aria-selected", "true"); h[idx].scrollIntoView({ block: "nearest" }); }
  }
  function close() { list.hidden = true; list.textContent = ""; idx = -1; input.setAttribute("aria-expanded", "false"); }
  function fetchNow() {
    var q = input.value.trim();
    if (!q) { close(); return; }
    var x = new XMLHttpRequest();
    x.open("GET", "/search?q=" + encodeURIComponent(q));
    x.onload = function () {
      if (input.value.trim() !== q || x.status !== 200) return;
      list.innerHTML = x.responseText; list.hidden = false;
      input.setAttribute("aria-expanded", "true"); setIdx(hits().length ? 0 : -1);
    };
    x.send();
  }
  input.addEventListener("input", function () { clearTimeout(timer); timer = setTimeout(fetchNow, 150); });
  input.addEventListener("focus", function () { if (input.value.trim() && list.hidden) fetchNow(); });
  input.addEventListener("keydown", function (e) {
    if (e.key === "ArrowDown") { e.preventDefault(); setIdx(idx + 1); }
    else if (e.key === "ArrowUp") { e.preventDefault(); setIdx(idx - 1); }
    else if (e.key === "Enter") { var h = hits(); if (h.length) { e.preventDefault(); window.location.href = h[Math.max(idx, 0)].href; } }
    else if (e.key === "Escape") { if (list.hidden) { input.blur(); box.classList.remove("open"); } else close(); }
  });
  document.addEventListener("keydown", function (e) {
    if (e.key !== "/" || e.ctrlKey || e.metaKey || e.altKey) return;
    var t = e.target, tag = t && t.tagName;
    if (tag === "INPUT" || tag === "TEXTAREA" || tag === "SELECT" || (t && t.isContentEditable)) return;
    e.preventDefault(); box.classList.add("open"); input.focus(); input.select();
  });
  // Cmd/Ctrl+K and Cmd/Ctrl+P toggle the search (works from the composer too). Browsers may reserve P combos.
  document.addEventListener("keydown", function (e) {
    if (!(e.metaKey || e.ctrlKey) || e.altKey || e.isComposing) return;
    var k = (e.key || "").toLowerCase();
    if (k !== "k" && k !== "p") return;
    if (k === "p" && e.shiftKey) return;  // Cmd/Ctrl+Shift+P is the command palette (palette.js)
    if (document.querySelector("dialog[open]")) return;
    e.preventDefault();
    if (document.activeElement === input) { close(); input.blur(); box.classList.remove("open"); return; }
    box.classList.add("open"); input.focus(); input.select();
  });
  // Shortcut forwarded by an embedded page (the chat aside iframe, keyfwd.js): same-origin child frames only.
  window.addEventListener("message", function (e) {
    var m = e.data;
    if (e.origin !== location.origin || !m || m.pm !== "shortcut" || m.act !== "search") return;
    if (e.source === window || !Array.prototype.some.call(document.querySelectorAll("iframe"), function (f) { return f.contentWindow === e.source; })) return;
    if (document.querySelector("dialog[open]")) return;
    window.focus(); box.classList.add("open"); input.focus(); input.select();
  });
  document.addEventListener("click", function (e) { if (!box.contains(e.target)) { close(); box.classList.remove("open"); } });
  if (tgl) tgl.addEventListener("click", function () { box.classList.toggle("open"); if (box.classList.contains("open")) input.focus(); });
})();
