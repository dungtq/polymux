/* dt theme (pm v7) project view, Stack tab (PRD.md §6.4): the project's compose services (state dot, health,
   ports, image), each service's logs in its row details (masked text in a scroll box), Up / Restart / Down for the
   whole stack (Down asks first: containers stop and are removed, volumes stay). No "Add a stack" (no catalogue).
   Data: GET <base>/stack -> {compose_file, services:[{name, container, state, health, ports, image}], running, total,
   urls, error}; GET <base>/stack/logs?service=&tail= (ANSI-stripped, masked text, set with textContent);
   POST <base>/stack/{up,restart,down} -> an HTML fragment (its text is the toast) or an error (pm7.why).
   Hooks: [data-pjops=stack][data-pj-base] (inside the frame's tab panel)
   > [data-pjs-body]. Helpers: js/pjops.js (pm7.pjops). */
(function () {
  'use strict';
  const pm7 = window.pm7, d = document;
  if (!pm7 || pm7.pjstack) return;
  const P = () => pm7.pjops;
  const TBL = 'pjs-svc', TAIL = 200;
  let busy = false;

  const base = (sec) => sec.getAttribute('data-pj-base') || '';
  const secOf = () => d.querySelector('[data-pjops="stack"]');
  const project = (sec) => sec.getAttribute('data-pj-name') || 'this project';

  /* docker state -> the dot (DESIGN §4: green running, yellow needs a look, red failed, grey idle, ring ended) */
  function dot(s) {
    if (s.health === 'unhealthy') return 'failed';
    if (s.state === 'running') return s.health === 'starting' ? 'waiting' : 'running';
    if (s.state === 'restarting') return 'waiting';
    if (s.state === 'dead') return 'failed';
    if (s.state === 'exited') return 'ended';
    return 'idle';
  }
  const word = (s) => (s.state || 'unknown') + (s.health ? ', ' + s.health : '');
  function healthCls(h) { return h === 'healthy' ? 'ok' : h === 'unhealthy' ? 'bad' : h ? 'warn' : 'muted'; }

  async function op(action) {
    const sec = secOf();
    if (!sec || busy) return;
    if (action === 'down') {
      const yes = await pm7.confirm({ title: 'Stop the stack of ' + project(sec) + '?', ok: 'Down', danger: true,
        body: 'Runs docker compose down: the containers stop and are removed; volumes and data folders stay. ' +
          'Up starts them again.' });
      if (!yes) return;
    }
    busy = true;
    sec.setAttribute('aria-busy', 'true');
    const label = { up: 'Up', restart: 'Restart', down: 'Down' }[action];
    try { await P().post(base(sec) + '/stack/' + action, {}, label); }
    finally { busy = false; sec.removeAttribute('aria-busy'); load(sec, true); }
  }

  async function logs(row, again) {
    const sec = secOf(), id = row.getAttribute('data-row-id'), det = row.nextElementSibling;
    const box = det && det.matches('.p-det') ? det.querySelector('.p-detc') : null;
    if (!sec || !box || (!again && box.hasAttribute('data-pjs-logs'))) return;
    box.setAttribute('data-pjs-logs', '');
    const H = P().h, pre = H('pre.pj-log', { tabindex: '0', 'aria-label': 'Logs of ' + id }, 'Loading the logs…');
    box.replaceChildren(pre, H('div.p-dacts', null, H('span.note', null, 'Last ' + TAIL + ' lines of ' + id +
      ', masked. Follow live logs in a terminal.'), H('span.sp'),
    P().btn('Reload', { icon: 'refresh', run: () => logs(row, true) })));
    let r = null;
    try {
      r = await pm7.fetch(base(sec) + '/stack/logs?' + new URLSearchParams({ service: id, tail: String(TAIL) }));
    } catch (e) { r = null; }
    if (!r || !r.ok) { pre.textContent = await pm7.why(r, 'Logs'); return; }
    const text = (await r.text()).replace(/\s+$/, '');
    pre.textContent = text || 'No log lines yet.';
    pre.scrollTop = pre.scrollHeight;
  }
  d.addEventListener('pm7:det', (e) => {
    const row = e.target;
    if (e.detail && e.detail.open && row.closest && row.closest('[data-p-table="' + TBL + '"]')) logs(row, false);
  });

  function tools(sec, v) {
    const X = P(), H = X.h, run = v.running, n = v.total;
    const urls = (v.urls || []).filter((u) => /^https:\/\/[A-Za-z0-9.-]+$/.test(u)).slice(0, 3);
    return H('div.p-tools', null, H('span.chip', { title: 'Compose file' }, X.icon('box'), H('span', null,
      v.compose_file)), H('span.note', null, n ? run + ' of ' + n + ' running' : 'no containers yet'),
    urls.map((u) => H('a.pj-url', { href: u, target: '_blank', rel: 'noopener noreferrer' }, u.slice(8))),
    H('span.sp'), H('span.pj-btns', null,
      X.btn('Up', { icon: 'play', run: () => op('up'), title: 'docker compose up -d: create and start the services' }),
      X.btn('Restart', { icon: 'refresh', run: () => op('restart'), disabled: !n,
        title: n ? 'docker compose restart' : 'Nothing to restart' }),
      X.btn('Down', { icon: 'stop', cls: 'danger', run: () => op('down'), disabled: !n,
        title: n ? 'docker compose down (asks first; volumes stay)' : 'Nothing is up' })));
  }
  function services(v) {
    const X = P(), H = X.h;
    const rows = [X.gh([{ sr: 'State' }, { label: 'Service' }, { label: 'Image', cls: 'hide-n' },
      { label: 'Ports', cls: 'hide-s' }, { label: 'Health', cls: 'hide-n' }, { sr: 'Actions' }])];
    v.services.forEach((s) => {
      const w = word(s);
      rows.push(X.row(s.name, [X.dot(dot(s), w), X.name(s.name, { title: s.container }),
        X.cell(s.image, 'mono muted hide-n'), X.cell(s.ports || '–', 'mono hide-s'),
        X.cell(s.health || '–', 'hide-n ' + healthCls(s.health)),
        X.meta([w, s.ports ? ' · ' + s.ports : '']),
        X.acts(X.detBtn(TBL, s.name, 'Logs of ' + s.name, { icon: 'list', title: 'Logs', cls: 'keep' }))],
      { title: s.name + ': ' + w }));
      rows.push(X.det(TBL, s.name, 'Logs of ' + s.name));
    });
    if (!v.services.length) rows.push(X.empty('No containers yet: Up creates and starts them.'));
    return [X.table(TBL, 'Services', 'pj-svc', rows, { list: 'pjs-services', keys: { enter: 'det' },
      labels: { enter: 'Show the logs' } }),
    H('p.p-foot', null, 'Down asks first and keeps volumes. pm never edits the compose file.')];
  }

  async function load(sec, again) {
    if (!sec) return;
    const body = sec.querySelector('[data-pjs-body]'), X = P();
    if (!body) return;
    if (!again && !body.childElementCount) body.replaceChildren(X.h('p.muted.pj-load', null, 'Loading the stack…'));
    let r = null;
    try { r = await pm7.fetch(base(sec) + '/stack'); } catch (e) { r = null; }
    if (!r || !r.ok) {
      body.replaceChildren(X.table('pjs-none', 'Stack', 'pj-svc', [X.empty(await pm7.why(r, 'Stack'))]));
      return;
    }
    let v = null;
    try { v = await r.json(); } catch (e) { v = null; }
    if (!v) return;
    const out = [];
    if (!v.compose_file) {
      out.push(X.table('pjs-none', 'Stack', 'pj-svc', [X.empty('No compose stack in ' + project(sec) +
        ': its folder has no docker-compose.yml or compose.yaml.')]));
    } else {
      out.push(tools(sec, v));
      if (v.error) out.push(X.ban('bad', v.error + '. Docker may be stopped, or this user may not reach it.'));
      out.push(...services(v));
    }
    const a = d.activeElement, fRow = a && body.contains(a) && a.closest('[data-row-id]');
    const fid = fRow ? fRow.getAttribute('data-row-id') : null;
    body.replaceChildren(...out);
    if (pm7.table) pm7.table.restore(body);
    if (fid) { const r2 = body.querySelector('[data-row-id="' + CSS.escape(fid) + '"]'); if (r2) r2.focus(); }
  }

  pm7.pjstack = { load: () => load(secOf(), true) };
  pm7.ready(() => { if (pm7.pjops) pm7.pjops.tab('stack', load); });
})();
