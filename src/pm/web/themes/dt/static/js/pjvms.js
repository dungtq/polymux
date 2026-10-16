/* dt theme (pm v7) project view, VMs tab (PRD.md §6.4). The list is server-rendered (GET <base>/vms -> dt
   _project_vms.html, view model pm.web.dt.pjops) into #pj-vms; this file loads it on first show and after an
   action, runs the row's visible Start / Shut down, and fills a row's details with the read-only console address and
   the snapshots (Revert: plain confirm; Delete snapshot: typed, it cannot be undone). ⋯ items (Snapshot, Force off,
   Delete VM) are parts.js menu posts; "Revert to a snapshot…" is the event pjv:revert handled here.
   Data: GET <base>/vms/<vm>/console -> {vm, state, url, read_only}; GET <base>/vms/<vm>/snapshots -> {vm,
   snapshots:[{name, created, state, current}], protected, manage}; POST <base>/vms/<vm>/{start,shutdown},
   .../snapshots (form snap), .../snapshots/<snap>/{revert,delete} -> an HTML fragment (its text is the toast).
   Hooks: [data-pjops=vms][data-pj-base] (inside the frame's tab panel)
   > #pj-vms[hx-get][hx-trigger=pm7:pjv-load]; rows [data-row-id]
   (VM name); [data-pjv-post][data-pjv-what] buttons; [data-pjv-live][data-pjv-vm] in the details. */
(function () {
  'use strict';
  const pm7 = window.pm7, d = document;
  if (!pm7 || pm7.pjvms) return;
  const P = () => pm7.pjops;
  let busy = false;

  const secOf = () => d.querySelector('[data-pjops="vms"]');
  const base = () => { const s = secOf(); return s ? s.getAttribute('data-pj-base') || '' : ''; };
  const vmUrl = (vm) => base() + '/vms/' + encodeURIComponent(vm);
  function reload() {
    const box = d.getElementById('pj-vms');
    if (box && window.htmx) window.htmx.trigger(box, 'pm7:pjv-load');
  }

  async function act(url, what) {
    if (busy) return null;
    busy = true;
    try { return await P().post(url, {}, what); } finally { busy = false; reload(); }
  }
  async function getJson(url, what) {
    let r = null;
    try { r = await pm7.fetch(url); } catch (e) { r = null; }
    if (!r || !r.ok) return { error: await pm7.why(r, what) };
    try { return await r.json(); } catch (e) { return { error: what + ': bad answer' }; }
  }

  /* ---------- snapshots ---------- */
  function revert(vm, s) {
    pm7.confirm({ title: 'Revert ' + vm + ' to ' + s.name + '?', ok: 'Revert', danger: true,
      body: vm + ' goes back to the state of snapshot ' + s.name + (s.created ? ' (' + s.created + ')' : '') +
        '. Changes made since then are lost; the snapshot itself stays.' })
      .then((yes) => { if (yes) act(vmUrl(vm) + '/snapshots/' + encodeURIComponent(s.name) + '/revert', 'Revert'); });
  }
  function dropSnap(vm, s) {
    pm7.confirm({ title: 'Delete snapshot ' + s.name + ' of ' + vm + '?', ok: 'Delete', typed: s.name,
      body: 'The snapshot is removed from libvirt; the VM itself does not change.' })
      .then((yes) => { if (yes) act(vmUrl(vm) + '/snapshots/' + encodeURIComponent(s.name) + '/delete', 'Delete'); });
  }
  function snapList(vm, sv) {
    const X = P(), H = X.h, can = sv.manage && !sv.protected;
    const why = !sv.manage ? 'VM management is off (vm_manage)' : sv.protected ? 'protected VM' : '';
    if (!sv.snapshots.length) return H('p.muted', null, 'No snapshots of ' + vm + ' yet.');
    return H('ul.pj-snaps', { 'aria-label': 'Snapshots of ' + vm }, sv.snapshots.map((s) => H('li', null,
      H('span.mono.nm', null, s.name), s.current ? H('span.chip', { title: 'The current snapshot' }, 'current') : null,
      H('span.muted', null, [s.created, s.state].filter(Boolean).join(' · ')), H('span.sp'),
      X.btn('Revert', { icon: 'refresh', disabled: !can, title: why || 'Revert ' + vm + ' to ' + s.name,
        run: () => revert(vm, s) }),
      X.btn('Delete', { icon: 'trash', cls: 'danger', disabled: !can, title: why || 'Delete snapshot ' + s.name,
        run: () => dropSnap(vm, s) }))));
  }
  async function live(row) {
    const vm = row.getAttribute('data-row-id'), det = row.nextElementSibling;
    const box = det && det.querySelector('[data-pjv-live]');
    if (!box) return;
    const X = P(), H = X.h;
    box.replaceChildren(H('p.muted', null, 'Loading console and snapshots…'));
    const [cv, sv] = await Promise.all([getJson(vmUrl(vm) + '/console', 'Console'),
      getJson(vmUrl(vm) + '/snapshots', 'Snapshots')]);
    const con = cv.error ? H('dd.bad', null, cv.error)
      : cv.url ? H('dd', null, H('code.pj-url', null, cv.url), ' ', X.btn('Copy', { icon: 'copy',
        run: () => navigator.clipboard.writeText(cv.url).then(() => pm7.toast('Copied'),
          () => pm7.toast('Copy failed', '', { error: true })) }))
        : H('dd.muted', null, cv.state === 'running' ? 'no graphical console' : 'only while running');
    box.replaceChildren(
      H('dl.p-kv', null, H('dt', null, 'Console'), con),
      H('p.muted.pj-note', null, 'Read-only address: open it in a VNC / SPICE viewer. pm never proxies a console or ' +
        'sends keystrokes.'),
      H('h3.pj-h3', null, 'Snapshots'),
      sv.error ? H('p.bad', null, sv.error) : snapList(vm, sv),
      sv.error ? null : H('div.p-dacts', null, X.btn('Take a snapshot', { icon: 'clock',
        disabled: !(sv.manage && !sv.protected), title: 'snapshot-create-as --atomic; the name is pm-<UTC time>',
        run: () => act(vmUrl(vm) + '/snapshots', 'Snapshot') })));
  }
  async function revertMenu(row) {
    const vm = row.getAttribute('data-row-id');
    const sv = await getJson(vmUrl(vm) + '/snapshots', 'Snapshots');
    if (sv.error) { pm7.toast(sv.error, '', { error: true }); return; }
    const s = sv.snapshots.find((x) => x.current) || (sv.snapshots.length === 1 ? sv.snapshots[0] : null);
    if (s) { revert(vm, s); return; }
    if (!sv.snapshots.length) { pm7.toast('No snapshots of ' + vm + ' yet'); return; }
    if (pm7.table) pm7.table.det(row, true);   // several and none current: pick one in the details
  }

  d.addEventListener('click', (e) => {
    const b = e.target && e.target.closest ? e.target.closest('#pj-vms [data-pjv-post]') : null;
    if (!b) return;
    e.preventDefault();
    act(b.getAttribute('data-pjv-post'), b.getAttribute('data-pjv-what') || 'VM');
  });
  d.addEventListener('pm7:det', (e) => {
    const row = e.target;
    if (e.detail && e.detail.open && row.closest && row.closest('#pj-vms')) live(row);
  });
  d.addEventListener('pjv:revert', (e) => {
    const row = e.target && e.target.closest ? e.target.closest('#pj-vms [data-row-id]') : null;
    if (row) revertMenu(row);
  });

  pm7.pjvms = { load: reload };
  pm7.ready(() => { if (pm7.pjops) pm7.pjops.tab('vms', reload); });
})();
