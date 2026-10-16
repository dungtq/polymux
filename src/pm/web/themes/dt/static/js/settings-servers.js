/* dt Settings › Remote servers: add → scan → compare the fingerprint → pin, Re-pin (the server's name typed), Install
   pm's key with a one-time password (never stored: the field is cleared when the request is built and when the
   dialog closes). Markup: templates/settings/_security.html (rows [data-row-id=<server id>] with data-s-srv-name,
   -user, -addr; the add form #s-srv-add; dialog#s-install). Owns no global; uses pm7.settings (js/settings.js),
   pm7.confirm / dialog / layers (layers.js), pm7.reply / fail (core.js).
   Hooks: ⋯ items fire s:pin, s:repin, s:install on their trigger (pm.web.dt.settings.server_rows); the row's
   button[data-s-pin]; button[data-s-addsrv] shows the add form; s:submitted {form: "s-srv-add", name} (from
   js/settings-forms.js) starts the scan of the new server once the section has reloaded.
   Core routes (Accept JSON): POST /settings/servers/{id}/scan -> {fp, host, port, pinned} | {error};
   /pin {fp}; /repin {fp, confirm: name}; /install {user, password} -> {ok, notice}. */
(function () {
  'use strict';
  const pm7 = window.pm7, d = document;
  const S = () => pm7.settings;
  const SEC = '#s-sec-servers';
  const base = (row) => '/settings/servers/' + encodeURIComponent(row.getAttribute('data-row-id'));

  function body(row, j, repin) {
    const name = row.getAttribute('data-s-srv-name');
    const kids = [pm7.el('p', null, 'Host ', pm7.el('b', null, name + ' (' + j.host + ':' + j.port + ')'), ' offers:'),
      pm7.el('p.mono', null, j.fp)];
    if (repin && j.pinned) kids.push(pm7.el('p', null, 'Pinned now: ', pm7.el('span.mono', null, j.pinned)));
    kids.push(pm7.el('p.x-dlg-hint', null, 'Compare it with ', pm7.el('code', null,
      'ssh-keygen -lf /etc/ssh/ssh_host_ed25519_key.pub'), ' on that host before you pin it.'));
    return pm7.el('div', null, kids);
  }
  async function pin(row, repin) {
    const name = row.getAttribute('data-s-srv-name');
    const r = await S().post(base(row) + '/scan', {});
    let j = null;
    if (r && r.ok) { try { j = await r.clone().json(); } catch (e) { j = null; } }
    if (!j || j.error || !j.fp) { pm7.fail('Scan of ' + name + ' failed', r); return; }
    if (repin && j.fp === j.pinned) { pm7.toast(name + ': the host key has not changed'); return; }
    const ok = await pm7.confirm({ title: repin ? 'Replace the host key of ' + name + '?' : 'Pin the host key of ' +
      name + '?', body: body(row, j, repin), ok: repin ? 'Replace key' : 'Pin key', danger: !!repin,
    typed: repin ? name : undefined, opener: row.querySelector('.p-mbtn') });
    if (!ok) return;
    const r2 = await S().post(base(row) + (repin ? '/repin' : '/pin'),
      repin ? { fp: j.fp, confirm: name } : { fp: j.fp });
    const a = await pm7.reply(r2);
    if (!a.ok) { pm7.fail('Host key not pinned', r2); return; }
    pm7.toast(a.notice || 'Host key pinned');
    S().refresh(SEC);
  }

  /* ---------- install pm's key (one-time password) ---------- */
  function install(row) {
    const dlg = d.getElementById('s-install'), f = dlg && pm7.$('form', dlg);
    if (!f) return;
    const name = row.getAttribute('data-s-srv-name');
    pm7.$('[data-s-install-body]', dlg).textContent = 'Enter the password for ' + row.getAttribute('data-s-srv-addr') +
      ' once. pm logs in one time to add its public key to ' + name + '.';
    f.elements.user.value = row.getAttribute('data-s-srv-user') || '';
    f.elements.password.value = '';
    f.setAttribute('data-s-for', row.getAttribute('data-row-id'));
    pm7.dialog(dlg, { focus: f.elements.password, opener: row.querySelector('.p-mbtn'),
      onClose: () => { f.elements.password.value = ''; } });
  }
  async function sendInstall(f) {
    const dlg = f.closest('dialog'), sid = f.getAttribute('data-s-for') || '';
    const row = d.querySelector(SEC + ' [data-row-id="' + CSS.escape(sid) + '"]');
    if (!row) return;
    const form = { user: f.elements.user.value.trim(), password: f.elements.password.value };
    f.elements.password.value = '';
    if (pm7.layers) pm7.layers.close(dlg);
    const r = await S().post(base(row) + '/install', form);
    form.password = '';
    const a = await pm7.reply(r);
    if (!a.ok) pm7.fail('Key not installed', r); else pm7.toast(a.notice || 'Key installed');
    S().refresh(SEC);
  }

  /* ---------- events ---------- */
  const rowOf = (e) => (e.target && e.target.closest ? e.target.closest(SEC + ' [data-row-id]') : null);
  d.addEventListener('s:pin', (e) => { const r = rowOf(e); if (r) pin(r, false); });
  d.addEventListener('s:repin', (e) => { const r = rowOf(e); if (r) pin(r, true); });
  d.addEventListener('s:install', (e) => { const r = rowOf(e); if (r) install(r); });
  d.addEventListener('click', (e) => {
    const t = e.target && e.target.closest ? e.target : null;
    if (!t) return;
    const b = t.closest(SEC + ' [data-s-pin]');
    if (b) { const r = b.closest('[data-row-id]'); if (r) pin(r, false); return; }
    const add = t.closest('[data-s-addsrv]');
    if (add) {
      const f = d.getElementById('s-srv-add');
      if (!f) return;
      f.hidden = !f.hidden;
      add.setAttribute('aria-expanded', f.hidden ? 'false' : 'true');
      if (!f.hidden) f.elements.name.focus();
    }
  });
  d.addEventListener('submit', (e) => {
    if (e.target && e.target.matches && e.target.matches('form[data-s-installform]')) {
      e.preventDefault();
      sendInstall(e.target);
    }
  });
  /* add → scan: once the section has reloaded with the new row, its host key is scanned and shown for pinning */
  d.addEventListener('s:submitted', (e) => {
    const x = e.detail || {};
    if (x.form !== 's-srv-add' || !x.name) return;
    const once = (ev) => {
      const sec = d.querySelector(SEC);
      if (!sec || !(ev.target === sec || sec.contains(ev.target))) return;
      d.removeEventListener('htmx:afterSettle', once);
      const row = pm7.$$(SEC + ' [data-row-id]').find((r) => r.getAttribute('data-s-srv-name') === x.name);
      if (row) pin(row, false);
    };
    d.addEventListener('htmx:afterSettle', once);
  });
})();
