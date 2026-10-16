/* pm UI glue. External file only (strict CSP): no inline handlers anywhere. */
(function () {
  'use strict';

  // --- service worker (static assets only; see /sw.js) ---
  if ('serviceWorker' in navigator) {
    window.addEventListener('load', function () {
      navigator.serviceWorker.register('/sw.js', { scope: '/' }).catch(function () {});
    });
  }

  // --- SSE fallback: when the stream errors, poll the inbox until it recovers ---
  var pollTimer = null;
  function startPolling() {
    if (pollTimer) return;
    pollTimer = setInterval(function () {
      document.querySelectorAll('#inbox, #pane').forEach(function (el) {
        if (window.htmx) window.htmx.trigger(el, 'pm:refresh');
      });
    }, 5000);
  }
  function stopPolling() {
    if (pollTimer) { clearInterval(pollTimer); pollTimer = null; }
  }
  document.addEventListener('pm:live-error', startPolling);
  document.addEventListener('pm:live-open', stopPolling);

  // --- error replies (JSON {"detail": ...}) land in a flash area as readable text ---
  function esc(t) { return window.pm.esc(t); }
  document.addEventListener('htmx:beforeSwap', function (e) {
    var d = e.detail, t = d && d.target, x = d && d.xhr;
    if (!t || !x || x.status < 400 || !t.matches) return;
    var body = String(d.serverResponse || '');
    if (/^\s*</.test(body)) return;  // already an HTML fragment
    if (t.matches('#chat, #pane') && t.cloneNode) {
      // a polled panel got an error (agent gone): keep its polling attributes, show a note
      var keep = t.cloneNode(false);
      keep.innerHTML = '<p class="muted">' + (x.status === 404 ? 'This agent is gone (its pane no longer exists).'
        : 'Could not refresh (' + x.status + ').') + '</p>';
      d.serverResponse = keep.outerHTML; d.shouldSwap = true;
      return;
    }
    if (!t.matches('#flash, .flash-inline')) return;
    var m = 'Request failed (' + x.status + ')';
    try { var j = JSON.parse(body); if (j && typeof j.detail === 'string' && j.detail) m = j.detail; } catch (err) { /* keep generic */ }
    d.serverResponse = '<p class="flash err">' + esc(m) + '</p>';
    d.shouldSwap = true;  // isError stays true so form[data-clear] keeps the draft
  });

  // --- submit lock: disable the submit button while a launch/dispatch request is in flight ---
  function lockBtn(f) { return f && f.matches && f.matches('form[data-submit-lock]') ? f.querySelector('button[type=submit]') : null; }
  document.addEventListener('htmx:beforeRequest', function (e) {
    var b = lockBtn(e.detail && e.detail.elt);
    if (!b) return;
    if (b.disabled) { e.preventDefault(); return; }
    b.dataset.label = b.textContent;
    b.textContent = e.detail.elt.getAttribute('data-submit-lock') || 'Working…';
    b.disabled = true;
  });
  document.addEventListener('htmx:afterRequest', function (e) {
    var f = e.detail && e.detail.elt, b = lockBtn(f);
    if (!b || b.dataset.label === undefined) return;
    var ok = e.detail.successful;
    // keep it locked briefly after success so a double tap cannot start a second agent
    setTimeout(function () { b.textContent = b.dataset.label; b.disabled = false; }, ok ? 2500 : 0);
    if (ok) b.textContent = 'Done';
  });

  // --- launch / dispatch options: searchable model picker, variant, effort, permission modes ---
  (function () {
    var node = document.getElementById('launch-data'), data = {};
    try { data = JSON.parse(node ? node.getAttribute('data-json') : '{}'); } catch (e) { data = {}; }
    function opt(sel, value, text, extra) {
      var o = document.createElement('option');
      o.value = value; o.textContent = text;
      if (extra) Object.keys(extra).forEach(function (k) { o.setAttribute(k, extra[k]); });
      sel.appendChild(o); return o;
    }
    function fill(sel, rows) {  // rows: [[value, text]]
      sel.textContent = '';
      rows.forEach(function (r) { opt(sel, r[0], r[1]); });
    }
    Array.prototype.forEach.call(document.querySelectorAll('[data-launch-opts]'), function (box) {
      var form = box.closest('form');
      if (!form || !form.elements.kind) return;
      var kind = form.elements.kind, ms = box.querySelector('[data-model-select]');
      var search = box.querySelector('[data-model-search]'), count = box.querySelector('[data-model-count]');
      var vrow = box.querySelector('[data-variant-row]'), vs = box.querySelector('[data-variant-select]');
      var custom = box.querySelector('[data-model-custom]');
      var erow = box.querySelector('[data-effort-row]'), es = box.querySelector('[data-effort-select]');
      var mode = box.querySelector('[data-mode-select]'), mlabel = box.querySelector('[data-mode-label]');
      var xrow = box.querySelector('[data-extra-row]'), xs = box.querySelector('[data-extra-select]');
      var xlabel = box.querySelector('[data-extra-label]');
      var help = box.querySelector('[data-mode-help]'), warn = box.querySelector('[data-bypass-warn]');
      var ok = box.querySelector('[data-bypass-confirm]');
      var helpText = {}, bypassKeys = {};

      function fams() { return (data.models && data.models[kind.value]) || []; }
      function famOf(name) { return fams().filter(function (f) { return f.name === name; })[0]; }
      function renderModels() {
        var q = search.value.trim().toLowerCase(), keep = ms.value, all = fams(), shown = 0;
        ms.textContent = '';
        opt(ms, '', 'default (the agent\'s own)');
        all.forEach(function (f) {
          var hay = (f.name + ' ' + f.variants.map(function (v) { return v[0] + ' ' + v[1]; }).join(' ')).toLowerCase();
          if (q && hay.indexOf(q) < 0 && f.name !== keep) return;
          opt(ms, f.name, f.name + (f.variants.length > 1 ? ' (' + f.variants.length + ')' : '')); shown++;
        });
        opt(ms, 'custom', 'custom id…');
        ms.value = Array.prototype.some.call(ms.options, function (o) { return o.value === keep; }) ? keep : '';
        count.textContent = all.length ? (q ? shown + ' of ' + all.length + ' models match' : all.length + ' models; type to filter') : 'No model list from this CLI: use default or a custom id.';
        renderVariants();
      }
      function renderVariants() {
        var f = famOf(ms.value);
        custom.hidden = ms.value !== 'custom';
        custom.querySelector('input').disabled = ms.value !== 'custom';
        vrow.hidden = !f || f.variants.length < 2;
        vs.disabled = !f;
        if (f) fill(vs, f.variants.map(function (v) { return [v[0], v[0] === v[1] ? v[0] : v[0] + ' — ' + v[1]]; }));
        else vs.textContent = '';
        var eff = f ? f.efforts : ((data.modes[kind.value] || {}).efforts || []);
        if (ms.value === 'custom') eff = (data.modes[kind.value] || {}).efforts || [];
        var cur = es.value;
        fill(es, [['', 'default']].concat(eff.map(function (e) { return [e, e]; })));
        es.value = eff.indexOf(cur) >= 0 ? cur : '';
        erow.hidden = !eff.length; es.disabled = !eff.length;
      }
      function renderModes() {
        var spec = data.modes && data.modes[kind.value] || { modes: [], extra: null };
        helpText = {}; bypassKeys = {};
        mode.textContent = '';
        opt(mode, '', 'default (the agent\'s own)');
        spec.modes.filter(function (m) { return !m.bypass; }).forEach(function (m) { opt(mode, m.key, m.key); helpText[m.key] = m.help; });
        var bp = spec.modes.filter(function (m) { return m.bypass; });
        if (bp.length) {
          var g = document.createElement('optgroup');
          g.label = '⚠ Bypass (dangerous)'; g.setAttribute('data-bypass', '1');
          bp.forEach(function (m) { opt(g, m.key, m.key); helpText[m.key] = m.help; bypassKeys[m.key] = 1; });
          mode.appendChild(g);
        }
        mlabel.textContent = spec.mode_label || 'Permission mode';
        xrow.hidden = !spec.extra; xs.disabled = !spec.extra;
        if (spec.extra) {
          xlabel.textContent = spec.extra.label;
          fill(xs, [['', 'default']].concat(spec.extra.options.map(function (o) { return [o.key, o.key]; })));
          spec.extra.options.forEach(function (o) { helpText['x:' + o.key] = o.help; });
        }
      }
      function renderHelp() {
        var parts = [], b = !!bypassKeys[mode.value];
        var spec = data.modes && data.modes[kind.value];
        var hideX = !spec || !spec.extra || (kind.value === 'codex' && mode.value === 'bypass');
        xrow.hidden = hideX; xs.disabled = hideX;
        parts.push(mode.value ? mode.value + ': ' + (helpText[mode.value] || '') : 'Permission mode: the agent\'s own default.');
        if (!xrow.hidden && xs.value) parts.push(xs.value + ': ' + (helpText['x:' + xs.value] || ''));
        help.textContent = parts.join(' ');
        warn.hidden = !b;
        mode.classList.toggle('bypass-on', b);
        ok.disabled = !b; ok.required = b;
        if (!b) ok.checked = false;
      }
      kind.addEventListener('change', function () {
        search.value = ''; ms.value = ''; renderModels(); renderModes(); renderHelp();
      });
      search.addEventListener('input', renderModels);
      ms.addEventListener('change', renderVariants);
      mode.addEventListener('change', function () { ok.checked = false; renderHelp(); });
      xs.addEventListener('change', renderHelp);
      renderModels(); renderModes(); renderHelp();
    });
  })();

  // --- after a request: clear sent message, reload after a successful unlock ---
  document.addEventListener('htmx:afterRequest', function (evt) {
    var elt = evt.detail && evt.detail.elt;
    if (!elt || !elt.matches || !evt.detail.successful) return;
    if (elt.matches('form[data-clear]')) {
      var ta = elt.querySelector('textarea');
      if (ta) ta.value = '';
    }
    if (elt.matches('form[data-unlock]')) {
      setTimeout(function () { window.location.reload(); }, 400);
    }
  });
})();
