/* dt Audit page (PRD.md §6.10). Markup: templates/audit.html, _audit_list.html (#au-act), _client_errors.html
   (#au-ce); view model pm.web.dt.audit. The parts do the rest: filter.js (server filter box), parts.js (segments,
   switch, ⋯ menus with Copy), table.js (bands, details), listkeys.js (J K, Enter = details), htmx (every control swaps
   its component; the head comes along through hx-select-oob). This file adds only the page's own bits:
   - [data-au-run=<item id>] buttons in a row's details run that item of the row's ⋯ menu (pm7.menus.run): Copy.
   - [data-au-issue] (Client errors details): POST /p/pm/issues (title, body); after the server's OK a toast and the
     button becomes a link to the new issue (V7-CODE-5); a refusal toasts the server's reason (pm7.fail).
   - [data-au-clear] (Client errors head): runs its item (data-au-item, built in pm.web.dt.audit_errors) through
     the shared menu post pm7.menus.exec: a dialog (the audit log is untouched), POST /audit/client-errors/clear, a
     toast after the server's OK, then pm7:refresh on #au-ce (its own GET; the head comes along).
   - the ⋯ item "Save as pm issue" (event au:issue) runs the Save button of that row's details.
   - after a swap, the component's data-au-count goes into its tab's count (the tabs sit outside the swap). */
(function () {
  'use strict';
  const pm7 = window.pm7;
  if (!pm7 || pm7.audit) return;
  const d = document;

  function rowOf(el) {
    const r = el.closest('[data-row-id]');
    if (r && r.matches('.p-gr')) return r;
    const det = el.closest('.p-det[data-p-det-for]'), t = det && det.closest('.p-g');
    return t ? t.querySelector('.p-gr[data-row-id="' + CSS.escape(det.getAttribute('data-p-det-for')) + '"]') : null;
  }
  function run(btn) {
    const r = rowOf(btn), m = r && r.querySelector('[data-menu]');
    return !!m && !!pm7.menus && pm7.menus.run(m, btn.getAttribute('data-au-run'));
  }

  async function issue(btn) {
    if (btn.disabled) return;
    btn.disabled = true;
    btn.setAttribute('aria-busy', 'true');
    let r = null;
    try {
      r = await pm7.fetch('/p/pm/issues', { form: { title: btn.getAttribute('data-au-title') || '',
        body: btn.getAttribute('data-au-body') || '' } });
    } catch (err) { r = null; }
    const got = await pm7.reply(r), j = got.json;  // {ok, num, project, url}
    btn.removeAttribute('aria-busy');
    if (!got.ok || !j || !j.num) { btn.disabled = false; pm7.fail('Save as pm issue', r); return; }
    const url = typeof j.url === 'string' && j.url[0] === '/' && j.url[1] !== '/' ? j.url : '/p/pm#issue=' + j.num;
    pm7.toast('Saved as issue #' + j.num + ' in ' + (j.project || 'pm'));
    const a = pm7.el('a.x-btn.sm', { href: url, title: 'Open issue #' + j.num + ' of ' + (j.project || 'pm') },
      pm7.icon('open'), 'Open issue #' + j.num);
    btn.replaceWith(a);
    a.focus();
  }

  function clear(btn) {
    let it = null;
    try { it = JSON.parse(btn.getAttribute('data-au-item') || 'null'); } catch (err) { it = null; }
    return !!it && pm7.menus.exec(btn, it);
  }
  /* the row's ⋯ item "Save as pm issue" (event au:issue) clicks the button in that row's details */
  d.addEventListener('au:issue', (e) => {
    const r = e.target.closest && e.target.closest('.p-gr[data-row-id]'), t = r && r.closest('.p-g');
    const det = t && t.querySelector('.p-det[data-p-det-for="' + CSS.escape(r.getAttribute('data-row-id')) + '"]');
    const b = det && det.querySelector('[data-au-issue]');
    if (b) issue(b);
  });

  d.addEventListener('click', (e) => {
    const t = e.target;
    if (!t || !t.closest || !t.closest('#au-act, #au-ce, #au-head')) return;
    const b = t.closest('[data-au-run], [data-au-issue], [data-au-clear]');
    if (!b) return;
    e.preventDefault();
    if (b.hasAttribute('data-au-run')) run(b);
    else if (b.hasAttribute('data-au-issue')) issue(b);
    else clear(b);
  });

  function count() {
    const box = d.querySelector('[data-au-tab][data-au-count]');
    const c = box && d.querySelector('#p-tab-' + box.getAttribute('data-au-tab') + ' .c');
    if (!c) return;
    const n = box.getAttribute('data-au-count');
    c.textContent = n;
    if (box.getAttribute('data-au-tab') === 'errors') c.classList.toggle('bad', n !== '0');
  }
  d.addEventListener('htmx:afterSettle', count);

  pm7.audit = { run: run };
})();
