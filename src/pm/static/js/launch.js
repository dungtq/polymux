// Launch page: prefill last choices, worktree hints, lock both submit buttons while in flight. The prompt is the shared
// composer (composer("launch"): send key Ctrl/Cmd+Enter = the Launch button, attachments, @files, / commands).
(function () {
  'use strict';
  var node = document.getElementById('launch-data'), data = {};
  try { data = JSON.parse(node ? node.getAttribute('data-json') : '{}'); } catch (e) { data = {}; }
  var form = document.querySelector('form[action$="/launch"]');
  if (!form) return;

  function fire(el) { el.dispatchEvent(new Event('change', { bubbles: true })); }
  function hasOpt(sel, v) { return Array.prototype.some.call(sel.options, function (o) { return o.value === v; }); }
  function setSel(el, v) {
    if (!el || !v || el.disabled || !hasOpt(el, v)) return false;
    el.value = v; fire(el); return true;
  }

  // 1. prefill (pm.js has already built the pickers by the time this deferred script runs)
  var r = data.remembered || {}, f = form.elements;
  if (r.kind && hasOpt(f.kind, r.kind)) setSel(f.kind, r.kind);
  var box = form.querySelector('[data-launch-opts]');
  if (box) {
    var search = box.querySelector('[data-model-search]');
    var msel = f.model_choice;
    if (r.model_choice && !hasOpt(msel, r.model_choice) && search) {  // filtered out? clear the filter
      search.value = ''; search.dispatchEvent(new Event('input', { bubbles: true }));
    }
    if (setSel(msel, r.model_choice)) {
      setSel(f.variant, r.variant);
      if (r.model_choice === 'custom' && f.model) f.model.value = r.model || '';
    }
    setSel(f.effort, r.effort);
    setSel(f.permission_mode, r.mode);
    setSel(f.permission_extra, r.extra);
  }
  if (r.branch && f.branch) f.branch.value = r.branch;
  var ta = form.querySelector('[data-composer=launch] [data-cmp-input]');
  function typed(v) { ta.value = v; ta.dispatchEvent(new Event('input', { bubbles: true })); }  // composer: grow, state
  if (r.prompt && ta) typed(r.prompt);  // review prefill (editable)

  // 2. worktree hints
  var wt = data.worktree || {}, bi = f.branch, hint = form.querySelector('[data-branch-hint]');
  var dl = document.getElementById('branch-list');
  (wt.branches || []).forEach(function (b) {
    var o = document.createElement('option'); o.value = b;
    if (wt.taken && wt.taken[b]) o.label = 'checked out: ' + wt.taken[b];
    dl.appendChild(o);
  });
  function hintText() {
    var b = bi.value.trim();
    hint.classList.remove('branch-hint-warn');
    if (!b) { hint.textContent = 'Empty: the agent runs in the project directory.'; return; }
    var path = (wt.base || '') + '/' + b.replace(/\//g, '-');
    var exists = (wt.branches || []).indexOf(b) >= 0;
    var taken = wt.taken && wt.taken[b];
    if (taken) { hint.textContent = 'Branch ' + b + ' is already checked out in ' + taken + '; launch will be refused.'; hint.classList.add('branch-hint-warn'); return; }
    hint.textContent = (exists ? 'Existing branch ' : 'New branch ') + b + ' in a worktree at ' + path;
  }
  bi.addEventListener('input', hintText); hintText();

  // 2b. prompt templates fill the textarea (only when it is empty or still holds a template)
  var tsel = form.querySelector('[data-template]');
  var tpl = data.templates || [], lastTpl = '';
  if (tsel && ta) tsel.addEventListener('change', function () {
    var t = tpl[parseInt(tsel.value, 10)];
    function fill() { lastTpl = t ? t.text : ''; typed(lastTpl); if (t) ta.focus(); }
    if (ta.value && ta.value !== lastTpl) {
      window.pmConfirm({ title: 'Replace the prompt text?', confirmLabel: 'Replace' }).then(function (ok) { if (ok) fill(); else tsel.value = ''; });
      return;
    }
    fill();
  });

  // 3. keep both buttons locked while a request is in flight (pm.js locks only the first)
  var btns = form.querySelectorAll('button[type=submit]');
  document.addEventListener('htmx:beforeRequest', function (e) {  // values are already collected here
    if (e.detail && e.detail.elt === form) btns.forEach(function (b) { b.disabled = true; });
  });
  document.addEventListener('htmx:afterRequest', function (e) {
    if (e.detail && e.detail.elt === form)
      setTimeout(function () { btns.forEach(function (b) { b.disabled = false; }); }, e.detail.successful ? 2500 : 0);
  });
})();

// 3b. inbox popup: the project picker reloads the framed form for the chosen project; the hint's
// "Launch in <project>" button (launch_redirects) switches the picker to that project
(function () {
  var pick = document.querySelector('[data-project-pick]');
  if (!pick) return;
  function reload() { location.href = '/p/' + encodeURIComponent(pick.value) + '/launch?embed=1&from=inbox'; }
  pick.addEventListener('change', reload);
  var sw = document.querySelector('[data-project-switch]');
  if (sw) sw.addEventListener('click', function () { pick.value = sw.getAttribute('data-project-switch'); reload(); });
})();

// 4. popup mode (?embed=1, framed by launchpop.js): Cancel closes it, a launch hands off to the parent page.
(function () {
  'use strict';
  if (window.parent === window || !/[?&]embed=1\b/.test(location.search)) return;
  function tell(msg) { try { window.parent.postMessage(Object.assign({ pm: 'launchpop' }, msg), location.origin); } catch (e) { /* ignore */ } }
  var form = document.querySelector('form[action$="/launch"]');
  var cancel = document.querySelector('[data-launch-cancel]');
  if (cancel) cancel.addEventListener('click', function (e) { e.preventDefault(); tell({ act: 'close' }); });
  document.addEventListener('keydown', function (e) { if (e.key === 'Escape') tell({ act: 'close' }); });
  document.addEventListener('click', function (e) {  // links never navigate the frame: the parent opens them
    var a = e.target.closest('a[href]');
    if (!a || e.defaultPrevented || e.button || e.ctrlKey || e.metaKey || e.shiftKey || a.hasAttribute('data-launch-cancel')) return;
    var u; try { u = new URL(a.getAttribute('href'), location.href); } catch (x) { return; }
    if (u.origin !== location.origin) return;
    e.preventDefault();
    tell(/^\/a\/[a-z0-9-]+$/.test(u.pathname) ? { act: 'open', href: u.pathname } : { act: 'nav', href: u.pathname + u.search });
  });
  document.addEventListener('htmx:beforeOnLoad', function (e) {
    var d = e.detail || {};
    if (!form || d.elt !== form || !d.xhr) return;
    var to = d.xhr.getResponseHeader('HX-Redirect');
    var m = to && /^(\/a\/[a-z0-9-]+)(\?.*)?$/.exec(to);
    if (m) { e.preventDefault(); tell({ act: 'open', href: m[1] }); }
  });
  document.addEventListener('htmx:afterRequest', function (e) {
    var d = e.detail || {};
    if (form && d.elt === form && d.successful && !(d.xhr && d.xhr.getResponseHeader('HX-Redirect')))
      tell({ act: 'stay' });  // close at once: the list adds the agent on pm:agent_added
  });
})();

// Kind picker: the select carries the chosen kind's identity colour (data-kind -> --k, v4.css), and the prompt's
// "/" list (slash.js reads the composer's data-slash) follows the launch form's kind: /plan, /advisor of that agent.
(function () {
  'use strict';
  var node = document.getElementById('launch-data'), cat = {};
  try { cat = JSON.parse(node ? node.getAttribute('data-json') : '{}').slash || {}; } catch (e) { cat = {}; }
  function sync(el) {
    if (!el || !el.value) return;
    el.setAttribute('data-kind', el.value);
    var root = el.form && el.form.matches('form[action$="/launch"]') && el.form.querySelector('[data-composer=launch]');
    if (root) root.setAttribute('data-slash', JSON.stringify(cat[el.value] || []));
  }
  document.addEventListener('change', function (e) {
    var t = e.target; if (t && t.tagName === 'SELECT' && t.name === 'kind') sync(t);
  });
  Array.prototype.forEach.call(document.querySelectorAll('select[name=kind]'), sync);
})();

// 5. attachments: the composer (composer-attach.js) uploads to /p/<name>/image and keeps the saved paths on its chips.
// The box sits inside this form, so the paths are added to the prompt here (chat-send.js does it for agent composers),
// and a launch waits for its uploads: capture phase, so the submit locks (pm.js, above) never run for a refused send.
(function () {
  'use strict';
  var form = document.querySelector('form[action$="/launch"]'), root = form && form.querySelector('[data-composer=launch]');
  var at = window.pm && window.pm.composerAttach;
  if (!root || !at) return;
  var ta = root.querySelector('[data-cmp-input]');
  document.addEventListener('htmx:configRequest', function (e) {
    if (!e.detail || e.detail.elt !== form) return;
    var paths = at.chipsReady(root).map(function (c) { return c.dataset.path; });
    if (!paths.length) return;
    var typed = ta.value.replace(/\s+$/, '');
    e.detail.parameters.prompt = (typed ? typed + '\n' : '') + paths.join('\n');
  });
  document.addEventListener('htmx:beforeRequest', function (e) {
    if (!e.detail || e.detail.elt !== form || !at.chipList(root).some(function (c) { return c.classList.contains('is-uploading'); })) return;
    e.preventDefault(); e.stopPropagation();
    window.pm.notify('Still uploading...', { kind: 'err', target: root.querySelector('[data-cmp-flash]') });
  }, true);
})();
