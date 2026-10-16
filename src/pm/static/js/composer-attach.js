/* Composer attachments, per root ([data-composer] with an upload URL): attach button, clipboard image button, paste
 * (images, and long text as a pasted-text.md / .txt file once it has more than data-paste-lines lines), drag and drop.
 * Each file uploads at once (pm.fetch, CSRF header) to the root's data-upload URL, or its [data-cmp-upload] form's
 * action, as the file field of that form (default "image"); it shows as a chip in [data-cmp-chips] and its saved path
 * goes out with the message (chat-send.js; Launch: launch.js). Works for any root, also one without an agent (Launch:
 * /p/<name>/image, a bare input[data-cmp-file] because the box sits inside the Launch form).
 * data-attach="inline" (quick chat: claude -p reads no files): attach, paste and drop read text files (up to 200 KB) into
 * the message at the cursor instead, and the paste-text button inserts the clipboard text; nothing uploads.
 * pm.composerAttach = {chipList(root), chipsReady(root), syncRequired(root), upload(root, files)}.
 * External file (strict CSP); after composer.js. */
(function () {
  'use strict';
  // Pure helpers (exported for tests): line count and md-vs-txt decision for pasted text.
  function countLines(text) { return String(text).split('\n').length; }
  function pastedName(text) {
    var md = /^\s*#/.test(text) || /^(- |\* |```|\|)/m.test(text);
    return md ? 'pasted-text.md' : 'pasted-text.txt';
  }
  window.pmComposer = { countLines: countLines, pastedName: pastedName };
  if (typeof document === 'undefined') return;
  var pm = window.pm, d = document, MAX = 10 * 1024 * 1024, MAX_TEXT = 200 * 1024;
  var TEXT_EXT = /\.(txt|md|markdown|json|jsonl|csv|tsv|log|py|js|mjs|ts|tsx|jsx|sh|bash|zsh|fish|yaml|yml|toml|ini|conf|cfg|env|html|css|xml|sql|go|rs|c|h|cpp|java|rb|php|diff|patch)$/i;

  function rootOf(el) { return el && el.closest ? el.closest('[data-composer]') : null; }
  function fileInput(root) { return root && root.querySelector('[data-cmp-upload] input[type=file], input[type=file][data-cmp-file]'); }
  function inline(root) { return !!root && root.getAttribute('data-attach') === 'inline'; }
  function uploadUrl(root) {
    var f = root.querySelector('[data-cmp-upload]');
    return root.getAttribute('data-upload') || (f ? f.getAttribute('action') : '');
  }
  function slot(root) { return root.querySelector('[data-cmp-flash]'); }
  function say(root, msg) { pm.notify(msg, { kind: 'err', target: slot(root) }); }
  function chipList(root) {
    var bar = root && root.querySelector('[data-cmp-chips]');
    return bar ? Array.prototype.slice.call(bar.querySelectorAll('.catt')) : [];
  }
  function chipsReady(root) {
    return chipList(root).filter(function (c) { return !c.classList.contains('is-uploading') && c.dataset.path; });
  }
  // Keep the textarea `required` (when it was) only while there is no finished attachment; composer.js follows `input`.
  function syncRequired(root) {
    var ta = root && root.querySelector('[data-cmp-input]');
    if (!ta) return;
    if (ta.__req === undefined) ta.__req = ta.required;
    if (chipsReady(root).length) ta.removeAttribute('required'); else if (ta.__req) ta.setAttribute('required', '');
    ta.dispatchEvent(new Event('input', { bubbles: true }));
  }
  function docIcon() {
    var NS = 'http://www.w3.org/2000/svg', svg = d.createElementNS(NS, 'svg');
    [['viewBox', '0 0 24 24'], ['width', '22'], ['height', '22'], ['fill', 'none'], ['stroke', 'currentColor'], ['stroke-width', '2'],
      ['stroke-linecap', 'round'], ['stroke-linejoin', 'round'], ['aria-hidden', 'true']].forEach(function (a) { svg.setAttribute(a[0], a[1]); });
    ['M14 2H6a2 2 0 0 0-2 2v16a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2V8z', 'M14 2v6h6', 'M8 13h8M8 17h6'].forEach(function (p) {
      var n = d.createElementNS(NS, 'path'); n.setAttribute('d', p); svg.appendChild(n);
    });
    return svg;
  }
  function span(cls, text) { var s = d.createElement('span'); s.className = cls; if (text) s.textContent = text; return s; }
  function makeChip(root, file, lines) {
    var bar = root.querySelector('[data-cmp-chips]');
    if (!bar) return null;
    var chip = d.createElement('div'), thumb = span('catt-t'), name = span('catt-n', file.name || 'screenshot');
    chip.className = 'catt is-uploading';
    var x = d.createElement('button');
    x.type = 'button'; x.className = 'catt-x'; x.title = 'Remove'; x.textContent = '×';
    x.setAttribute('aria-label', 'Remove attachment');
    x.addEventListener('click', function () {
      chip.remove();
      syncRequired(root);
      var s = slot(root);
      if (s && !chipList(root).length && s.querySelector('[data-image-path]')) s.textContent = '';
    });
    chip.appendChild(thumb); chip.appendChild(name); chip.appendChild(x);
    if (/^image\//.test(file.type) && window.FileReader) {
      var rd = new FileReader();  // data: URL (CSP allows data:, not blob:)
      rd.onload = function () {
        var img = d.createElement('img');
        img.alt = ''; img.src = String(rd.result);
        thumb.textContent = ''; thumb.appendChild(img);
      };
      rd.readAsDataURL(file);
    } else {
      thumb.appendChild(docIcon());
      if (lines) {
        name.textContent = '';
        name.appendChild(span('catt-nm', file.name)); name.appendChild(span('catt-ln', lines + ' lines'));
        name.classList.add('catt-n2');
      }
    }
    bar.appendChild(chip);
    return chip;
  }
  function uploadOne(root, file, lines) {
    if (file.size > MAX) { say(root, 'File too large (max 10 MB).'); return Promise.resolve(); }
    var form = root.querySelector('[data-cmp-upload]'), inp = fileInput(root), url = uploadUrl(root);
    if (!url) return Promise.resolve();
    var chip = makeChip(root, file, lines);
    syncRequired(root);
    var fd = form ? new FormData(form) : new FormData();
    fd.set((inp && inp.name) || 'image', file, file.name || 'screenshot.png');
    return pm.fetch(url, { method: 'POST', body: fd, csrf: 'header' })
      .then(function (r) { return r.text().then(function (t) { return { ok: r.ok, t: t }; }); })
      .then(function (res) {
        pm.notify(res.t, { html: true, target: slot(root) });  // "File attached." (kept while it names a path)
        var m = /data-image-path="([^"]+)"/.exec(res.t);
        if (res.ok && m && chip) { chip.dataset.path = m[1].replace(/&amp;/g, '&'); chip.classList.remove('is-uploading'); }
        else if (chip) chip.remove();
        syncRequired(root);
      })
      .catch(function () {
        if (chip) chip.remove();
        syncRequired(root);
        say(root, 'Upload failed.');
      });
  }
  function upload(root, files) {
    var chain = Promise.resolve();
    Array.prototype.slice.call(files || []).forEach(function (f) { chain = chain.then(function () { return uploadOne(root, f, f.pmLines); }); });
    return chain;
  }
  function canAttach(root) { return !!root && !!uploadUrl(root) && !!root.querySelector('[data-cmp-chips]'); }

  // ---- inline: text files into the message ----
  function isText(f) { return (/^text\//.test(f.type) || /json|xml|yaml|javascript/.test(f.type) || TEXT_EXT.test(f.name || '')) && f.size <= MAX_TEXT; }
  function put(root, text) {
    var r = pm.composer.insert(root, text), ta = root.querySelector('[data-cmp-input]'), n = ta ? ta.maxLength : 0;
    if (!r.ok) say(root, 'The message is full (' + n + ' characters).'); else if (r.cut) say(root, 'Cut at ' + n + ' characters.');
  }
  function readText(root, files) {
    var skipped = [], s = slot(root);
    if (s) s.textContent = '';
    Array.prototype.slice.call(files || []).forEach(function (f) {
      if (!isText(f)) { skipped.push(f.name || 'file'); return; }
      var name = f.name || 'pasted', rd = new FileReader();
      rd.onload = function () {
        var txt = String(rd.result || '');
        if (txt.indexOf('\0') >= 0) say(root, 'Skipped ' + name + ' (binary).');
        else put(root, '[' + name + ']\n```\n' + txt.replace(/\n$/, '') + '\n```\n');
      };
      rd.onerror = function () { say(root, 'Could not read ' + name + '.'); };
      rd.readAsText(f);
    });
    if (skipped.length) say(root, 'Skipped ' + skipped.join(', ') + ': only text files (up to 200 KB) go into the message here.');
  }
  function take(root, files) { if (inline(root)) readText(root, files); else upload(root, files); }
  function pasteText(root) {
    var ta = root.querySelector('[data-cmp-input]');
    function no(m) { say(root, m); if (ta) ta.focus(); }
    if (!navigator.clipboard || !navigator.clipboard.readText) { no('Clipboard access is not available here; paste with Ctrl/Cmd+V.'); return; }
    navigator.clipboard.readText().then(function (t) { if (t) put(root, t); else no('The clipboard has no text.'); })
      .catch(function () { no('Clipboard access was denied; paste with Ctrl/Cmd+V.'); });
  }

  d.addEventListener('click', function (e) {
    var t = e.target, root = rootOf(t);
    if (!root || !t.closest) return;
    if (t.closest('[data-attach]')) { var f = fileInput(root); if (f) f.click(); return; }
    if (t.closest('[data-paste-text]')) { pasteText(root); return; }
    if (!t.closest('[data-paste-img]') || !canAttach(root)) return;
    if (!navigator.clipboard || !navigator.clipboard.read) {
      say(root, 'Clipboard images are not available here; paste with Ctrl/Cmd+V in the message box.');
      return;
    }
    navigator.clipboard.read().then(function (items) {
      for (var i = 0; i < items.length; i++) {
        for (var j = 0; j < items[i].types.length; j++) {
          if (/^image\//.test(items[i].types[j])) {
            return items[i].getType(items[i].types[j]).then(function (blob) {
              return uploadOne(root, new File([blob], 'screenshot.' + (blob.type.split('/')[1] || 'png'), { type: blob.type }));
            });
          }
        }
      }
      say(root, 'No image on the clipboard.');
    }).catch(function () { say(root, 'Clipboard access was denied.'); });
  });
  d.addEventListener('change', function (e) {
    var inp = e.target, root = rootOf(inp);
    if (!root || !inp.matches || !inp.matches('[data-cmp-upload] input[type=file], input[type=file][data-cmp-file]') || !inp.files || !inp.files.length) return;
    var files = Array.prototype.slice.call(inp.files);
    inp.value = '';
    take(root, files);
  });
  // paste into the box: images upload like the attach button; long text becomes a file (inline: text files go in)
  d.addEventListener('paste', function (e) {
    var t = e.target, root = rootOf(t);
    if (!root || !t.matches || !t.matches('[data-cmp-input]') || !e.clipboardData) return;
    var fs = e.clipboardData.files;
    if (inline(root)) { if (fs && fs.length) { e.preventDefault(); readText(root, fs); } return; }
    if (!canAttach(root)) return;
    var items = e.clipboardData.items || [];
    for (var k = 0; k < items.length; k++) {
      var f = items[k].kind === 'file' && items[k].getAsFile();
      if (f) { e.preventDefault(); upload(root, [f]); return; }
    }
    var n = parseInt(root.getAttribute('data-paste-lines'), 10), text = e.clipboardData.getData('text/plain');
    if (isNaN(n)) n = 10;
    if (n > 0 && text && countLines(text) > n) {
      e.preventDefault();
      var pf = new File([text], pastedName(text), { type: 'text/plain' });
      pf.pmLines = countLines(text);
      upload(root, [pf]);
    }
  });
  // drag and drop files onto the composer
  function hasFiles(e) { var t = e.dataTransfer && e.dataTransfer.types; return !!t && Array.prototype.indexOf.call(t, 'Files') !== -1; }
  d.addEventListener('dragover', function (e) {
    var root = rootOf(e.target);
    if (!(canAttach(root) || inline(root)) || !hasFiles(e)) return;
    e.preventDefault();
    root.classList.add('is-drop');
  });
  d.addEventListener('dragleave', function (e) {
    var root = rootOf(e.target);
    if (root && (!e.relatedTarget || !root.contains(e.relatedTarget))) root.classList.remove('is-drop');
  });
  d.addEventListener('drop', function (e) {
    var root = rootOf(e.target);
    if (!root) return;
    root.classList.remove('is-drop');
    if (!(canAttach(root) || inline(root)) || !hasFiles(e)) return;
    e.preventDefault();
    take(root, e.dataTransfer.files);
  });
  pm.composerAttach = { chipList: chipList, chipsReady: chipsReady, syncRequired: syncRequired, upload: upload };
})();
