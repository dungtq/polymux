/* dt attachments (docs/themes/dt/PARTS.md §15 "Attachments"): the one way a dt form uploads a file and shows it as a
   removable chip. Used by the Launch prompt box (js/launch-prompt.js: upload at once, chip .up meanwhile) and the
   Add issue dialog (js/backlog.js: chips for the picked files, upload on Add).
   Globals: pm7.attach = {
     upload(base, file) -> Promise<path>   POST <base>/image (multipart field image; pm7.fetch adds the CSRF token).
                                          The server answers <p class="flash ok" data-image-path="..."> (parsed, never
                                          inserted); a failure rejects with Error("<name>: <reason>") (the answer's
                                          .flash.err text, else pm7.why).
     chip(box, name, onRemove) -> chip    span.chip.att[data-att] appended to box: the name (ellipsis, title) and an
                                          × button (label "Remove <name>") that removes the chip and calls onRemove.
                                          Classes: .up while uploading (muted, "…", no ×), .bad for a failed file.
     done(chip, path)                     the upload finished: drops .up, sets data-path and the title.
     paths(root) -> [path]                the data-path of every finished chip under root.
     uploading(root) -> bool              a chip under root is still .up.
     wire(zone, onFiles)                  paste and drop of files on zone: a paste inside zone (any field in it) or a
                                          drop on it that carries files calls onFiles([File]) and is not inserted as
                                          text; zone gets .drop while files are dragged over it. A paste without
                                          files (text) is left alone. Listeners sit on zone, never on the document. }
   Look: .chip.att in css/parts.css. */
(function () {
  'use strict';
  const pm7 = window.pm7, d = document;
  if (!pm7 || pm7.attach) return;

  async function upload(base, file) {
    const name = file.name || 'pasted file', fd = new FormData();
    fd.append('image', file, name);
    let r = null, html = '';
    try { r = await pm7.fetch(base + '/image', { form: fd }); html = await r.clone().text(); } catch (e) { r = null; }
    const doc = new DOMParser().parseFromString(html, 'text/html');
    const ok = doc.querySelector('[data-image-path]'), p = doc.querySelector('p');
    const path = r && r.ok && ok ? ok.getAttribute('data-image-path') || '' : '';
    if (path) return path;
    if (r && r.ok) throw new Error(name + ': no stored path in the answer');
    const said = p ? p.textContent.trim().slice(0, 160) : '';
    throw new Error(said ? name + ': ' + said : await pm7.why(r, name));
  }
  function chip(box, name, onRemove) {
    const x = pm7.el('button.x-ib.sm', { type: 'button', 'aria-label': 'Remove ' + name, title: 'Remove' },
      pm7.icon('x') || '×');
    const c = pm7.el('span.chip.att', { 'data-att': true, title: name }, pm7.el('span.ell', null, name), x);
    x.addEventListener('click', () => { c.remove(); if (onRemove) onRemove(); });
    box.appendChild(c);
    return c;
  }
  function done(c, path) {
    c.classList.remove('up');
    c.setAttribute('data-path', path);
    c.title = path;
  }
  function paths(root) {
    return pm7.$$('[data-att][data-path]', root).map((c) => c.getAttribute('data-path'));
  }
  function uploading(root) { return !!root.querySelector('[data-att].up'); }
  function wire(zone, onFiles) {
    const hasFiles = (e) => !!(e.dataTransfer && Array.from(e.dataTransfer.types || []).indexOf('Files') >= 0);
    zone.addEventListener('paste', (e) => {
      const fl = e.clipboardData && e.clipboardData.files;
      if (!fl || !fl.length) return;
      e.preventDefault();
      onFiles(Array.from(fl));
    });
    zone.addEventListener('dragover', (e) => { if (hasFiles(e)) { e.preventDefault(); zone.classList.add('drop'); } });
    zone.addEventListener('dragleave', (e) => { if (!zone.contains(e.relatedTarget)) zone.classList.remove('drop'); });
    zone.addEventListener('drop', (e) => {
      zone.classList.remove('drop');
      if (!hasFiles(e)) return;
      e.preventDefault();
      onFiles(Array.from(e.dataTransfer.files || []));
    });
  }

  pm7.attach = { upload: upload, chip: chip, done: done, paths: paths, uploading: uploading, wire: wire };
})();
