/* dt theme (pm v7) first paint: loaded blocking in <head>, before any CSS-dependent paint, so the sidebar width, the
   colour mode and the user's looks never flash (L-1). Global: pm7.look = {apply(), prefs(json), tok(key), load(),
   rail(value?)}. Event: pm7:look on document after prefs are applied (detail.area 'prefs').
   This browser (Settings › Appearance, Display: README A23), localStorage pm.v7.*:
     shell.rail ("rail" | "open") -> html.side-rail; shell.railmode ("tab" = per tab; missing / "all" = one state for
       every tab) with shell.railtab = {"<kind>:<id>" | "home": "rail" | "open"} (the newest RAILTABS tabs; a tab
       without an entry follows shell.rail); the tab is html[data-tab] (base.html, the server's current tab).
       pm7.look.rail() reads the state for this tab, rail(v) stores it (sidebar.js ⌘B, settings.js): the one place
       that knows these keys; shell.mode ("light" | "dark" | "auto") -> html[data-theme];
     aside.open ("1", docked widths only) -> html.aside; aside.pct (20-50) -> --aside-pct;
     font.ui / font.chat / font.mono (system | sans | serif | mono | same (chat) | an installed family) -> --font-ui,
       --font-chat, --font-mono (+ classic content's --chat-font); font.size (90-125 %) -> the root font size (%) and
       --fs-k (tokens.css: --fs-body x --fs-k); chat.size (10-32 px) -> --chat-size; cmp.size (10-32 px) ->
       --compose-size + html[data-compose-size] (classic composer.css, removal debt); keys.hints = "0" ->
       html.no-khints; keys.toast (key | action | off) ->
       html[data-ktoast]; side.sess = "0" -> html.no-sess.
   All devices (Settings › Display, Chat colours: GET /dt/prefs, pm.web.dt.prefs), mirrored in pm.v7.prefs.cache
   {rev, t, prefs} so they apply before paint too: colour keys -> their tokens (TOK) on <html>; msg_bg page | tint |
   custom -> --bub-asst-bg (tint = --bub-asst-tint); run_status -> html[data-wvmode]; kind_edge / kind_pill /
   model_pill off -> html.no-kedge / no-kpill / no-mpill; tool_pill -> html.tp-fill | tp-both; src_you / src_ntfy ->
   html[data-src-you|ntfy]; cmp_phone open -> html.cmp-open (the session composer shows on phones, css/session.css).
   The mirror is refreshed after load (when older than 30 s), on pm7:ui {area: "prefs"} with a newer rev and on
   pm7:resync (both from live.js), with pm7.fetch (core.js, there by then). Written by sidebar.js (rail), keys.js
   (mode), aside.js (aside), settings.js (the rest). Storage may be blocked: then the defaults stay. */
(function () {
  'use strict';
  var root = document.documentElement, P = 'pm.v7.';
  var pm7 = window.pm7 = window.pm7 || {};
  function get(k) { try { return window.localStorage.getItem(P + k); } catch (e) { return null; } }
  function put(k, v) { try { window.localStorage.setItem(P + k, v); } catch (e) { /* blocked */ } }
  /* named choices are the browser's generic families (the stacks themselves stay in tokens.css) */
  var FAM = { system: 'system-ui', sans: 'sans-serif', serif: 'serif', mono: 'monospace' };
  var TOK = { col_dot_run: '--dot-run', col_dot_wait: '--dot-wait', col_dot_fail: '--dot-fail',
    col_dot_idle: '--dot-idle', col_dot_end: '--dot-end', col_badge_un: '--badge-un', col_badge_need: '--badge-need',
    col_user_bg: '--bub-user-bg', col_user_bd: '--bub-user-bd',
    col_k_claude: '--k-claude', col_k_codex: '--k-codex', col_k_devin: '--k-devin',
    col_k_cursor_agent: '--k-cursor-agent', col_k_antigravity: '--k-antigravity', col_tool_shell: '--tool-shell',
    col_tool_read: '--tool-read', col_tool_edit: '--tool-edit', col_tool_web: '--tool-web',
    col_tool_agent: '--tool-agent', col_tool_mcp: '--tool-mcp', col_msg_bg: '--bub-asst-bg' };
  var HEX = /^#[0-9a-f]{6}$/, RAILTABS = 40;

  function fam(v, dflt) {  // a known name, else one family typed by the user (specials stripped)
    if (!v) return dflt;
    if (FAM[v]) return FAM[v];
    var n = String(v).replace(/[^A-Za-z0-9 _.-]/g, '').trim();
    return n ? '"' + n + '",' + dflt : dflt;
  }
  function prop(k, v) { if (v) root.style.setProperty(k, v); else root.style.removeProperty(k); }
  function px(v) { var n = parseInt(v, 10); return n >= 10 && n <= 32 ? n + 'px' : ''; }

  /* sidebar rail / open: one state for all tabs (shell.rail) or, with shell.railmode = tab, one per tab */
  function railTabs() {
    var m;
    try { m = JSON.parse(get('shell.railtab') || '{}'); } catch (e) { return {}; }
    return m && typeof m === 'object' && !Array.isArray(m) ? m : {};
  }
  function rail(v) {
    var per = get('shell.railmode') === 'tab', k = per ? root.getAttribute('data-tab') || 'home' : '', m;
    if (v === undefined) {
      m = per ? railTabs()[k] : '';
      return m === 'rail' || m === 'open' ? m : get('shell.rail') === 'rail' ? 'rail' : 'open';
    }
    v = v === 'rail' ? 'rail' : 'open';
    if (!per) { put('shell.rail', v); return v; }
    m = railTabs();
    delete m[k];
    m[k] = v;  // insertion order = age (tab keys are never integer-like): the oldest go first
    Object.keys(m).slice(0, -RAILTABS).forEach(function (o) { delete m[o]; });
    put('shell.railtab', JSON.stringify(m));
    return v;
  }

  function browser() {
    var c = root.classList, ui = get('font.ui'), ch = get('font.chat'), mono = get('font.mono');
    c.toggle('side-rail', rail() === 'rail');
    var ap = +get('aside.pct');
    prop('--aside-pct', ap >= 20 && ap <= 50 ? ap + 'vw' : '');
    var m = get('shell.mode');
    root.setAttribute('data-theme', m === 'light' || m === 'dark' ? m : 'auto');
    prop('--font-ui', ui && ui !== 'system' ? fam(ui, FAM.system) : '');  // system = the token
    var chat = ch && ch !== 'same' ? fam(ch, FAM.system) : (ui ? 'var(--font-ui)' : '');
    prop('--font-chat', chat);
    prop('--chat-font', chat);
    prop('--font-mono', mono && mono !== 'mono' ? fam(mono, FAM.mono) : '');  // mono = the token
    var sz = +get('font.size'), k = sz >= 90 && sz <= 125 && sz !== 100 ? sz / 100 : 0;
    root.style.fontSize = k ? sz + '%' : '';  // rem sizes
    prop('--fs-k', k ? String(k) : '');  // --fs-body = its px size x --fs-k (tokens.css, README A25)
    prop('--chat-size', px(get('chat.size')));
    var cs = px(get('cmp.size'));
    prop('--compose-size', cs);
    if (cs) root.setAttribute('data-compose-size', ''); else root.removeAttribute('data-compose-size');
    c.toggle('no-khints', get('keys.hints') === '0');
    var kt = get('keys.toast');
    if (kt === 'action' || kt === 'off') root.setAttribute('data-ktoast', kt); else root.removeAttribute('data-ktoast');
    c.toggle('no-sess', get('side.sess') === '0');
  }

  function cache() { try { var j = JSON.parse(get('prefs.cache') || '{}'); return j && typeof j === 'object' ? j : {}; }
    catch (e) { return {}; } }
  function prefs(v) {
    var p = v || {}, c = root.classList;
    Object.keys(TOK).forEach(function (k) { if (k !== 'col_msg_bg') prop(TOK[k], HEX.test(p[k] || '') ? p[k] : ''); });
    var bg = p.msg_bg === 'page' ? 'var(--page)' : p.msg_bg === 'tint' ? 'var(--bub-asst-tint)'
      : p.msg_bg === 'custom' && HEX.test(p.col_msg_bg || '') ? p.col_msg_bg : '';
    prop('--bub-asst-bg', bg);
    root.setAttribute('data-wvmode', p.run_status === 'plain' || p.run_status === 'off' ? p.run_status : 'playful');
    c.toggle('no-kedge', p.kind_edge === false);
    c.toggle('no-kpill', p.kind_pill === false);
    c.toggle('no-mpill', p.model_pill === false);
    c.toggle('tp-fill', p.tool_pill === 'fill');
    c.toggle('tp-both', p.tool_pill === 'both');
    c.toggle('cmp-open', p.cmp_phone === 'open');
    ['you', 'ntfy'].forEach(function (s) {
      var st = p['src_' + s];
      if (st === 'grey' || st === 'border') root.setAttribute('data-src-' + s, st);
      else root.removeAttribute('data-src-' + s);
    });
    try { document.dispatchEvent(new CustomEvent('pm7:look', { detail: { area: 'prefs' } })); } catch (e) { /* old */ }
  }
  /* a GET /dt/prefs (or POST) answer: store the mirror and apply it */
  function take(j) {
    if (!j || typeof j !== 'object' || typeof j.rev !== 'number') return;
    put('prefs.cache', JSON.stringify({ rev: j.rev, t: Date.now(), prefs: j.prefs || {} }));
    prefs(j.prefs);
  }
  function load() {
    if (!pm7.fetch) return;
    pm7.fetch('/dt/prefs').then(function (r) { return r.ok ? r.json() : null; })
      .then(take, function () { /* offline or signed out: keep the mirror */ });
  }

  if (get('aside.open') === '1' && window.innerWidth >= 900) root.classList.add('aside');  // first paint only
  browser();
  prefs(cache().prefs);
  pm7.look = { apply: browser, prefs: take, tok: function (k) { return TOK[k] || ''; }, load: load, rail: rail };
  document.addEventListener('DOMContentLoaded', function () {
    var b = document.body;  // the dt shell (body.x); the aside's frame leaves it to its parent
    if (window.frameElement || !b || !b.classList.contains('x') || b.classList.contains('x-embed')) return;
    if (Date.now() - (+cache().t || 0) > 30000) load();
  });
  document.addEventListener('pm7:ui', function (e) {
    var d = e.detail || {};
    if (d.area === 'prefs' && !(d.rev <= (+cache().rev || 0))) load();
  });
  document.addEventListener('pm7:resync', load);
})();
