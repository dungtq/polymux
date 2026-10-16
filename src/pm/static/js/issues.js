/* Project page, Issues tab of a tracker project.
 * #issues-box holds the tab body (GET /p/<name>/issues, swapped after every change; the add form element is
 * kept so typed text survives), .iss-view the open issue (GET /p/<name>/issues/<num>, replaces the list until
 * closed with x / Esc, like the Plans tab's plan view). Forms post with pm.fetch (X-Requested-With: JSON back);
 * without fetch they post normally and the server redirects to /p/<name>#issue=<num>.
 * Add box: Enter adds a one-liner; More shows a body field and image attach (picker, paste, drop), uploaded
 * through POST /p/<name>/image like the launch prompt box. External file only (strict CSP); no native popups. */
(function () {
  'use strict';
  var d = document, pm = window.pm;
  var box = d.getElementById('issues-box'), view = d.querySelector('#pj-p-issues .iss-view');
  if (!box || !view || !pm) return;
  var base = view.getAttribute('data-base'), XRW = { 'X-Requested-With': 'pm' }, curNum = 0;
  var DRAWER = /^#(chat|term|plan)=/;

  function setHash(h) {
    if (DRAWER.test(location.hash)) return;   /* the open chat aside owns the hash (drawer.js) */
    try { history.replaceState(null, '', location.pathname + location.search + h); } catch (e) { /* ignore */ }
  }
  function get(url) {
    return pm.fetch(url, { headers: XRW, cache: 'no-store' }).then(function (r) {
      if (!r.ok) throw new Error('http ' + r.status);
      return r.text();
    });
  }
  function frag(html) { return new DOMParser().parseFromString(html, 'text/html').body; }
  /* errors show under the add box (add, row checkboxes) or under the issue's buttons */
  function say(form, msg) {
    var p = (view.contains(form) ? view : box).querySelector('.iss-err');
    if (p) { p.textContent = msg || ''; p.hidden = !msg; }
  }

  /* ---- list ---- */
  function applyView() {
    var v = box.getAttribute('data-iss-view') === 'done' ? 'done' : 'open';
    box.querySelectorAll('.bl-seg [data-iss-view]').forEach(function (b) {
      b.setAttribute('aria-pressed', b.getAttribute('data-iss-view') === v ? 'true' : 'false');
    });
  }
  function counts() {
    var n = box.querySelector('.bl-seg [data-iss-view="open"] .bl-seg-n'), pill = d.querySelector('.pj-tab[data-pj-pane="pj-p-issues"] .pj-n');
    if (n && pill) pill.textContent = n.textContent;
  }
  function reload() {
    return get(box.getAttribute('data-src')).then(function (html) {
      var neu = frag(html), keep = box.querySelector('form.iss-add'), nf = neu.querySelector('form.iss-add');
      if (keep && nf) nf.replaceWith(keep);
      box.replaceChildren.apply(box, Array.prototype.slice.call(neu.childNodes));
      applyView(); counts();
    });
  }
  function flash(msg) {
    var f = d.getElementById('iss-flash');
    if (!f) return;
    f.textContent = msg; f.hidden = false;
    setTimeout(function () { f.hidden = true; }, 4000);
  }
  box.addEventListener('click', function (e) {
    var sb = e.target.closest('.bl-seg [data-iss-view]');
    if (sb) { box.setAttribute('data-iss-view', sb.getAttribute('data-iss-view')); applyView(); return; }
    var mb = e.target.closest('[data-iss-more]');
    if (mb) { more(mb.closest('form'), mb.getAttribute('aria-expanded') !== 'true'); return; }
    var a = e.target.closest('a[data-iss-open]');
    var row = e.target.closest('.iss-row');
    if (e.button || e.metaKey || e.ctrlKey || e.shiftKey || e.altKey) return;
    if (a) { e.preventDefault(); open(+a.getAttribute('data-iss-open')); return; }
    /* the row opens the issue, except its checkbox and links inside the title */
    if (row && !e.target.closest('form, a, button')) open(+row.getAttribute('data-iss-row'));
  });

  /* ---- detail ---- */
  function open(num, keepScroll) {
    if (!(num > 0)) return Promise.resolve();
    return get(base + '/issues/' + num).then(function (html) {
      view.replaceChildren.apply(view, Array.prototype.slice.call(frag(html).childNodes));
      view.hidden = false; box.hidden = true; curNum = num;
      setHash('#issue=' + num);
      var body = d.getElementById('pj-body');
      if (!keepScroll && body) body.scrollTop = 0;
    }).catch(function () {
      view.hidden = false; box.hidden = true; curNum = 0;
      view.innerHTML = '<p class="muted pj-note">Could not load issue #' + pm.esc(num) + '. <button type="button" class="linkbtn" data-iss-close>Back to the list</button></p>';
    });
  }
  function close() {
    if (view.hidden) return;
    view.hidden = true; view.textContent = ''; box.hidden = false; curNum = 0;
    setHash('#issues');
  }
  view.addEventListener('click', function (e) {
    if (e.target.closest('[data-iss-close]')) { close(); return; }
    var ed = e.target.closest('[data-iss-edit]'), cancel = e.target.closest('[data-iss-edit-cancel]');
    if (ed || cancel) {
      var f = view.querySelector('.iss-editf'), b = view.querySelector('.iss-body'), btn = view.querySelector('[data-iss-edit]');
      var on = !!ed && f.hidden;
      f.hidden = !on; if (b) b.hidden = on; if (btn) btn.setAttribute('aria-expanded', on ? 'true' : 'false');
      if (on) { var t = f.querySelector('input[name="title"]'); if (t) t.focus(); }
    }
  });
  /* Send to agent: a full navigation to /a/<id>#draft=... (drawer.js would put ?embed=1 after the hash) */
  d.addEventListener('click', function (e) {
    var a = e.target.closest && e.target.closest('a[data-issue-send]');
    if (!a || e.button || e.metaKey || e.ctrlKey || e.shiftKey || e.altKey) return;
    e.preventDefault(); e.stopPropagation();
    location.assign(a.getAttribute('href'));
  }, true);
  d.addEventListener('click', function (e) {   /* the Send menu closes on a click elsewhere */
    d.querySelectorAll('details.iss-send[open]').forEach(function (m) { if (!m.contains(e.target)) m.open = false; });
  });
  /* phones / touch: the open Send menu is a bottom sheet in the top layer (a manual popover), so the scrolling
     pane (a size container: position: fixed stays inside it) neither cuts it nor scrolls; Esc / a tap elsewhere
     close the <details> as above, which hides the sheet. Without popover support it stays a dropdown. */
  var SHEET = window.matchMedia ? window.matchMedia('(max-width: 699px), (pointer: coarse)') : null;
  function sheet(m) {
    var pop = m.querySelector('.iss-sendpop');
    if (!pop || !pop.showPopover) return;
    if (m.open && SHEET && SHEET.matches) {
      pop.setAttribute('popover', 'manual'); pop.classList.add('iss-sheet');
      try { pop.showPopover(); } catch (e) { pop.removeAttribute('popover'); pop.classList.remove('iss-sheet'); }
    } else if (pop.hasAttribute('popover')) {
      try { pop.hidePopover(); } catch (e) { /* already hidden */ }
      pop.removeAttribute('popover'); pop.classList.remove('iss-sheet');
    }
  }
  d.addEventListener('toggle', function (e) {   /* toggle does not bubble: capture */
    if (e.target.matches && e.target.matches('details.iss-send')) sheet(e.target);
  }, true);
  d.addEventListener('keydown', function (e) {
    if (e.key !== 'Escape' || e.defaultPrevented || d.querySelector('dialog[open]')) return;
    var m = d.querySelector('details.iss-send[open]');
    if (m) { m.open = false; m.querySelector('summary').focus(); return; }
    if (view.hidden || /^(INPUT|TEXTAREA|SELECT)$/.test((e.target && e.target.nodeName) || '')) return;
    close();
  });

  /* ---- mutations ---- */
  function post(form) {
    return pm.fetch(form.action, { method: 'POST', headers: XRW, cache: 'no-store', body: new URLSearchParams(new FormData(form)) })
      .then(function (r) {
        return r.json().catch(function () { return { ok: false, error: 'Request failed (' + r.status + ').' }; })
          .then(function (j) { if (!r.ok && j.ok !== false) j = { ok: false, error: j.detail || 'Request failed (' + r.status + ').' }; return j; });
      });
  }
  d.addEventListener('submit', function (e) {
    var form = e.target;
    if (e.defaultPrevented || !form.matches || !form.matches('#issues-box form, .iss-view form[data-iss-form]')) return;
    e.preventDefault();
    if (form.dataset.busy) return;
    if (form.classList.contains('iss-add') && pending(form)) { say(form, 'Waiting for the image upload...'); return; }
    form.dataset.busy = '1';
    var btns = form.querySelectorAll('button[type=submit]');
    btns.forEach(function (b) { b.disabled = true; });
    function end() { delete form.dataset.busy; btns.forEach(function (b) { b.disabled = false; }); }
    var adding = form.classList.contains('iss-add'), deleting = /\/delete$/.test(form.action), inView = view.contains(form);
    post(form).then(function (j) {
      end();
      if (!j.ok) { say(form, j.error || 'Failed.'); return; }
      if (adding) reset(form);
      var next = inView && !deleting ? open(j.num, true) : (deleting ? (close(), null) : null);
      return Promise.all([reload(), next]).then(function () {
        if (adding) flash('Added #' + j.num);   /* after the swap: the head (and its flash) is new */
        var row = adding && box.querySelector('[data-iss-row="' + j.num + '"]');
        if (row) { row.classList.add('bl-new'); if (row.scrollIntoView) row.scrollIntoView({ block: 'nearest' }); }
      }, function () { location.reload(); });   /* saved, but the list could not be fetched: show the server's state */
    }, function () {
      end();
      HTMLFormElement.prototype.submit.call(form);   /* the POST itself failed (network): plain POST, the server redirects back */
    });
  });

  /* ---- add box: More, image attach (picker / paste / drop) ---- */
  function more(form, on) {
    var m = form.querySelector('.iss-more'), b = form.querySelector('[data-iss-more]');
    m.hidden = !on; b.setAttribute('aria-expanded', on ? 'true' : 'false');
    if (on) form.querySelector('textarea').focus();
  }
  function chips(form) { return Array.prototype.slice.call(form.querySelectorAll('.iss-chip')); }
  function pending(form) { return chips(form).some(function (c) { return c.classList.contains('is-uploading'); }); }
  function syncPaths(form) {
    form.elements.images.value = chips(form).map(function (c) { return c.getAttribute('data-path') || ''; })
      .filter(Boolean).join('\n');
  }
  function reset(form) {
    form.reset(); chips(form).forEach(function (c) { c.remove(); }); syncPaths(form); more(form, false);
    say(form, ''); form.elements.title.focus();
  }
  function upload(form, file) {
    if (!/^image\/(png|jpeg|gif|webp)$/.test(file.type)) { say(form, file.name + ': only png, jpg, gif or webp images.'); return; }
    more(form, true);
    var chip = d.createElement('span'), name = d.createElement('span'), x = d.createElement('button');
    chip.className = 'iss-chip is-uploading'; name.textContent = file.name || 'image';
    x.type = 'button'; x.textContent = '×'; x.setAttribute('data-iss-unchip', ''); x.setAttribute('aria-label', 'Remove ' + name.textContent); x.title = 'Remove';
    chip.appendChild(name); chip.appendChild(x);
    form.querySelector('.iss-chips').appendChild(chip);
    var fd = new FormData(); fd.append('image', file, file.name || 'image');
    pm.fetch(form.getAttribute('data-img-url'), { method: 'POST', body: fd, csrf: 'header', headers: XRW })
      .then(function (r) { return r.text(); })
      .then(function (html) {
        var b = frag(html), ok = b.querySelector('[data-image-path]');
        if (!ok) throw new Error((b.textContent || '').trim() || 'Upload failed.');
        chip.setAttribute('data-path', ok.getAttribute('data-image-path')); chip.classList.remove('is-uploading');
        syncPaths(form);
      })
      .catch(function (err) { chip.remove(); say(form, String(err && err.message || 'Upload failed.')); });
  }
  function images(list) {
    return Array.prototype.filter.call(list || [], function (f) { return f && /^image\//.test(f.type); });
  }
  box.addEventListener('change', function (e) {
    if (!e.target.matches('[data-iss-file]')) return;
    var form = e.target.closest('form');
    images(e.target.files).forEach(function (f) { upload(form, f); });
    e.target.value = '';
  });
  box.addEventListener('click', function (e) {
    var x = e.target.closest('[data-iss-unchip]');
    if (!x) return;
    var form = x.closest('form'); x.closest('.iss-chip').remove(); syncPaths(form);
  });
  box.addEventListener('paste', function (e) {
    var form = e.target.closest && e.target.closest('form.iss-add'), cd = e.clipboardData;
    if (!form || !cd) return;
    var files = images(Array.prototype.map.call(cd.items || [], function (it) { return it.kind === 'file' ? it.getAsFile() : null; }));
    if (!files.length) return;
    e.preventDefault();
    files.forEach(function (f) { upload(form, f); });
  });
  box.addEventListener('dragover', function (e) {
    if (e.target.closest('form.iss-add') && e.dataTransfer && Array.prototype.indexOf.call(e.dataTransfer.types || [], 'Files') > -1) {
      e.preventDefault(); e.target.closest('form.iss-add').classList.add('is-drop');
    }
  });
  box.addEventListener('dragleave', function (e) {
    var f = e.target.closest && e.target.closest('form.iss-add');
    if (f && !f.contains(e.relatedTarget)) f.classList.remove('is-drop');
  });
  box.addEventListener('drop', function (e) {
    var form = e.target.closest('form.iss-add');
    if (!form || !e.dataTransfer) return;
    form.classList.remove('is-drop');
    var files = images(e.dataTransfer.files);
    if (!files.length) return;
    e.preventDefault();
    files.forEach(function (f) { upload(form, f); });
  });

  /* ---- #issue=N / #issues from a link or a reload (after projview.js has wired the tab) ---- */
  function fromHash() {
    var m = location.hash.match(/^#issue=(\d+)$/);
    if (!m && location.hash !== '#issues') return;
    var tab = d.querySelector('a[data-pj-pane="pj-p-issues"]');
    if (tab && tab.getAttribute('aria-selected') !== 'true') tab.click();
    if (m && +m[1] !== curNum) open(+m[1]); else if (!m) close();
  }
  if (d.readyState === 'loading') d.addEventListener('DOMContentLoaded', fromHash); else fromHash();
  window.addEventListener('hashchange', fromHash);
  applyView();
})();
