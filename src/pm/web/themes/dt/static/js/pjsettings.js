/* dt project view: project settings and git remotes (PARITY "Project settings", "Remotes config"). Markup: the dt
   overrides of the core's fragments templates/_project_settings.html (GET <base>/settings/panel) and
   _git_remotes.html (GET <base>/git/remotes, also inside the settings panel), shown in the project view's reader
   aside (pm7.pj.reader, js/project.js) with cl: false (dt markup, no classic box).
   - open: the head's Settings button ([data-pj-settings]), Git ▾ Remotes… (js/project.js calls pm7.pjs.open), and
     the hash keys #settings / #remotes on load and on hashchange (then dropped from the hash).
   - forms (form[data-pjs-form] in #pj-settings / #git-remotes): posted with pm7.fetch to the core's routes (project
     settings, links, remote add / set-url); the answer is the same panel with a flash ([data-pjs-flash] ok | bad),
     which replaces the panel in place. Toast only after the server's answer (V7-CODE-5): ok = the flash text, bad =
     an error toast and the form keeps what was typed; HTTP errors via pm7.fail. A saved settings form that changed
     a checkbox (what the page shows: tabs, Docs choices, the links row) reloads the page with #settings, which
     reopens the panel and shows the toast (one-shot pm7.store key pj.saved).
   - import (data-pjs-form=import): posts the chosen *.code-workspace id to <base>/settings/links/import; an answer
     "Linked N ..." with N > 0 reloads like a links save.
   - remove ([data-pjr-rm]): typed remote name (V7-SEC-6; the push remote, #pj-lay[data-pj-remote] = pm.toml
     push_remote, also needs confirm=<name> at the core), then POST <base>/git/remote/remove {name, confirm}.
   - the pm key ⋯ (copy, Generate key) is parts.js: its refresh reloads #git-remotes (hx-get on pm7:refresh).
   Globals: pm7.pjs = {open(what: 'settings' | 'remotes', opener)}. No timer, no keydown listener. */
(function () {
  'use strict';
  const pm7 = window.pm7, d = document;
  const SAVED = 'pj.saved', PANELS = '#git-remotes, #pj-settings';
  const lay = () => d.getElementById('pj-lay');
  const attr = (k) => { const l = lay(); return l ? l.getAttribute(k) || '' : ''; };

  const hashState = () => pm7.pj.hash.get(), setHash = (p) => pm7.pj.hash.set(p);  // js/project.js

  function open(what) {
    if (!lay() || !pm7.pj || !pm7.pj.reader) return false;
    const rem = what === 'remotes';
    return pm7.pj.reader.open({ title: (rem ? 'Git remotes · ' : 'Settings · ') + attr('data-pj'),
      url: attr('data-pj-base') + (rem ? '/git/remotes' : '/settings/panel'), href: '', cl: false });
  }

  function twin(form, node) {  // the same form in the fresh panel (set-url: the same remote's)
    const kind = form.getAttribute('data-pjs-form'), rname = (f) => { const i = pm7.$('[name=name]', f);
      return i ? i.value : ''; };
    return pm7.$$('form[data-pjs-form="' + kind + '"]', node).find((f) => kind !== 'set-url'
      || rname(f) === rname(form)) || null;
  }
  function keep(form, same) {  // a refused answer: the fresh form gets what the user typed
    pm7.$$('input:not([type=hidden]), textarea', form).forEach((i) => {
      const j = i.name && pm7.$$('[name="' + i.name + '"]', same)
        .find((x) => x.type !== 'checkbox' || x.value === i.value);
      if (!j) return;
      if (i.type === 'checkbox') j.checked = i.checked; else j.value = i.value;
    });
  }

  let busy = false;
  async function send(panel, url, body, what, reload, form) {  // reload: bool, or fn(flash text) -> bool
    if (busy) return;
    busy = true;
    let r;
    try { r = await pm7.fetch(url, { form: body }); } catch (e) { r = null; }
    busy = false;
    if (!r || !r.ok) { pm7.fail(what, r); return; }
    const doc = new DOMParser().parseFromString(await r.text(), 'text/html');
    const fresh = doc.getElementById(panel.id);
    if (!fresh) { pm7.toast(what + ': no answer', '', { error: true }); return; }
    const f = fresh.querySelector(':scope > [data-pjs-flash]');
    const good = !f || f.getAttribute('data-pjs-flash') === 'ok', text = f ? f.textContent.trim() : '';
    const node = d.importNode(fresh, true), inside = panel.contains(d.activeElement);
    panel.replaceWith(node);
    if (window.htmx) window.htmx.process(node);
    const same = form ? twin(form, node) : null;
    if (inside) {  // focus stays where it was: the same form's button, else the panel's first field
      const f = (same && pm7.$('button[type=submit]', same)) || pm7.$('input:not([type=hidden]), button', node);
      if (f) f.focus({ preventScroll: true });
    }
    if (!good) {
      if (same) keep(form, same);
      pm7.toast(what + ': ' + (text || 'refused'), '', { error: true });
      return;
    }
    if (typeof reload === 'function' ? reload(text) : reload) {
      pm7.store.set(SAVED, text || what);
      const p = hashState();
      p.set('settings', '');
      setHash(p);
      window.location.reload();
      return;
    }
    pm7.toast(text || what + ': done');
  }

  const WHAT = { settings: 'Project settings', links: 'Linked projects', import: 'Import workspace',
    add: 'Add remote', 'set-url': 'Set url' };
  d.addEventListener('submit', (e) => {
    const form = e.target;
    if (!form || !form.matches || !form.matches('form[data-pjs-form]')) return;
    const panel = form.closest(PANELS);
    if (!panel) return;
    e.preventDefault();
    if (!form.checkValidity()) { form.reportValidity(); return; }
    const kind = form.getAttribute('data-pjs-form');
    let changed = kind === 'settings' || kind === 'links'
      ? pm7.$$('input[type=checkbox]', form).some((i) => i.checked !== i.defaultChecked) : false;
    // the links row changes only if some were added; the text is the route's flash (routes/projectsettings.py)
    if (kind === 'import') changed = (r) => /^Linked [1-9]/.test(r);
    send(panel, form.getAttribute('action'), new URLSearchParams(new FormData(form)), WHAT[kind] || 'Save', changed,
      form);
  });

  async function remove(btn) {
    const panel = btn.closest('#git-remotes'), rn = btn.getAttribute('data-pjr-rm');
    if (!panel || !rn) return;
    const push = rn === attr('data-pj-remote');
    const ok = await pm7.confirm({ title: 'Remove remote ' + rn + ' of ' + attr('data-pj') + '?', typed: rn,
      ok: 'Remove remote', danger: true, opener: btn, undo: 'Add it again with its url to undo.',
      body: push ? 'pm pushes only to ' + rn + ': Fetch, Pull and Push stay off until you add it again.'
        : 'git forgets this remote and its url. The repository on the server does not change.' });
    if (!ok) return;
    send(panel, panel.getAttribute('data-pjr') + '/git/remote/remove', { name: rn, confirm: rn }, 'Remove remote');
  }

  d.addEventListener('click', (e) => {
    const t = e.target;
    if (!t || !t.closest || e.defaultPrevented || !lay()) return;
    let el;
    if ((el = t.closest('[data-pj-settings]'))) { e.preventDefault(); open('settings'); return; }
    if ((el = t.closest('[data-pjr-rm]'))) { e.preventDefault(); remove(el); }
  });

  function fromHash() {  // #settings / #remotes: open that panel, then drop the key from the hash
    if (!lay()) return;
    const p = hashState(), what = p.has('settings') ? 'settings' : p.has('remotes') ? 'remotes' : '';
    if (!what) return;
    p.delete('settings');
    p.delete('remotes');
    setHash(p);
    open(what);
  }

  pm7.pjs = { open: open };
  window.addEventListener('hashchange', fromHash);
  pm7.ready(() => {
    fromHash();
    const saved = lay() && pm7.store.get(SAVED);
    if (saved) { pm7.store.remove(SAVED); pm7.toast(saved); }
  });
})();
