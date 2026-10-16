# pm v7: shell contract (M1)

The fixed interface between the dt shell's Python (`src/pm/web/dt/`), its templates
(`src/pm/web/themes/dt/templates/`) and its JS (`src/pm/web/themes/dt/static/js/`). Written 2026-10-15
for milestone M1. Change it only together with every side that uses it. Look and geometry: `DESIGN.md`,
`LAYOUT.md`; the reference markup came from the design mock (not published).

## 1. Core contract

| Item | Contract |
|---|---|
| Theme choice | per request: `?ui=<name>` (registered only; remembered in cookie `pm_ui`) > cookie `pm_ui` > pm.toml `theme` > classic |
| Template context | `page_ctx` adds `ui_theme` (str), `ui_themes` (list of `(name, label)`), `request` |
| Plugin hook | `template_globals() -> dict`; keys start with `<plugin>_` (dt: `dt_shell`; page view models `dt_inbox`, `dt_ntfy`, `dt_session` in `src/pm/web/dt/{inbox,ntfy,session}.py`, menu items from `menus.py`, PARTS.md §9) |
| Hub event | type `ui`, payload `{"area": "tabs", "rev": int}`; `request.app.state.hub.publish("ui", {...})` from async code |
| Static | `/static/t/dt/<path>` versioned with `?v=` like `/static/...` |
| Archcheck | `tools/archcheck.py --static-dir DIR` (frontend rules + line ≤ 120 + no raw z-index) |
| uishot | `tools/uishot.py --ui dt` |

## 2. Python → templates: `dt_shell(ctx)`

A `jinja2.pass_context` global. Reads the template context (`request`, `nav`, `agent`, `embed`) and returns a
`Shell` (frozen dataclasses in `src/pm/web/dt/shell.py`; templates read attributes only, no logic beyond loops
and conditions):

```
Shell(nav: list[NavRow], foot: list[NavRow], groups: list[SessGroup], tabs: list[Tab], home: Tab,
      rev: int, need: int, running: int, total: int, chat: bool)
    chat: the page is a session's own page (`page_ref(path)` is set: /a/<id>[/...], /term/s<n>) -> `html.x-chatpg`
NavRow(key, href, label, icon, count: int | None, hot: bool, chord: str, cur: bool)
    built from the core's one nav list (`env.globals["NAV"]` = `render.NAV` + plugin `nav_items`): href and label
    come from the core item; dt keeps only `DT_NAV` (core key -> dt key, icon, chord, label override, foot) and
    its order (PRD §5). dt keys / core items / icons / chords:
    inbox (inbox) / inbox / "G I" · projects (projects) / folder / "G P" · tmux (tmux) / tmux / "G T" ·
    notifications (core "ntfy", "/ntfy", label "Notifications") / bell / "G N" · backlog (backlog) / list / "G B" ·
    agents (agents) / bot / "G A" · servers (servers) / server / "G S"
    foot: audit (audit) / shield / "" · settings (settings) / sliders / "mod+,"
    core items without a dt slot (plugin `nav_items`) follow the page nav in the core's order: icon "box", no
    chord, current when the page's `nav` is their key; a plugin key equal to a dt key is skipped; a dt slot whose
    core item is missing is not shown
    Notifications = the core "ntfy" item (`/ntfy`: all messages, agent and system = PRD §6.7's "one page", Source
    All). Classic's system-only `/notifications` page has no nav row (it is not in the core NAV); in dt it is the
    same page with Source System preselected for that load (the saved view `pm.v7.notifs.*` is one for both URLs),
    and both pages mark the Notifications row current. The bell (a menu button, `js/notifs.js`) shows the newest 6
    messages of every source from `/ntfy/panel?scope=all` and links to `/ntfy`
    counts: inbox = waiting + failed, projects = projects with a live agent, tmux = sessions; notifications None:
    the unread system count (`sidebadges.system_unread`, classic's `/inbox/unread` "system") reads the ntfy feed
    and the inbox store, too slow for a render (M2: the live event brings it)
    hot = the count is "needs you" (Inbox: waiting + failed)
SessGroup(project: str, href: str, dot: str, rows: list[SessRow])
SessRow(id, kind, state, href, label, since: int | None, open: bool, cur: bool, title: str, age: str)
    state: running | waiting | failed | idle | unknown; label = the agent id (shown, DESIGN §6.1); title = the
    window/agent name when it differs from the id, else "" (row `title` and `data-title` only, never shown);
    since = epoch of state_since; href = "/a/<id>"; open = in a tab; cur = this page is that agent;
    age = time in the state at render ("5s", "3m", "2h", "4d": first unit of `timefmt.duration`, = `ticker.js`),
    "" when since is unknown
Tab(kind: "home" | "agent" | "term", id: str, title: str, href: str, state: str, agent_kind: str, cur: bool,
    icon: str)
    home: title = the current page's label (nav key, else "pm"), icon = that nav row's icon (else "inbox"),
    href = that page, state ""; on an agent page that is not open as a tab: title = the agent id, icon "chat",
    state = that agent's state word ("ended" when gone; the phone title shows it with the context's project),
    and no nav row is current (its session row is); agent: title = the agent id, href "/a/<id>"; term: "/term/s<n>" for tmux session `$<n>`
    (`render.urlid`); a tab whose agent is gone keeps state "ended"; icon is "" except on home
```

Current tab = derived from the URL being rendered (agent page of an open tab → that tab; else home). The stored
`current` is used only to restore order/scroll on other devices.

## 3. Routes (`src/pm/web/dt/routes.py`, prefix `/dt`)

| Method | Path | Body (form, `csrf`) | Returns |
|---|---|---|---|
| GET | `/dt/side` | — | `shell/_sessions.html` fragment (ETag/204 via `render.fragment`) |
| GET | `/dt/tabs` | — | `shell/_tabs.html` fragment (the strip without the home tab's page title change) |
| GET | `/dt/tabs.json` | — | `{"rev", "tabs": [{kind, id, title, href, state}], "closed": [...]}` |
| POST | `/dt/tabs/open` | `kind`, `id` | strip fragment; focuses an already open tab |
| POST | `/dt/tabs/close` | `kind`, `id` | strip fragment; never the home tab |
| POST | `/dt/tabs/reopen` | — | strip fragment + header `X-Dt-Href` = the reopened tab's href (204 if none) |
| POST | `/dt/tabs/current` | `kind`, `id` | 204 |
| POST | `/dt/tabs/move` | `kind`, `id`, `before_kind`, `before_id` (empty = end) | strip fragment |
| POST | `/dt/tabs/close-all` | — | strip fragment + header `X-Dt-Closed` = number closed (Settings › Close all session tabs; all go to the reopen list) |
| GET | `/dt/prefs` | — | `{"rev", "prefs": {key: value}}` (v7 "all devices" prefs, `pm.web.dt.prefs`; stored keys only) |
| POST | `/dt/prefs` | `<key>=<value>` (several; `""` = back to the default) | `{"ok", "notice", "rev", "prefs"}`; 400 on an unknown key or a bad value (audited `denied`); `mutation_guard("dt.prefs.save")`, event `ui` `{"area": "prefs", "rev"}` |

GETs are plain `def` (threadpool: the tabs file is stat'ed / read). Every POST: `mutation_guard("dt.tabs.<verb>",
Tier.SAFE)`, audit line, store write in `executors.run_collectors`, then `hub.publish("ui", {"area": "tabs", "rev":
rev})`. A request whose UI theme is not dt (cookie `pm_ui` switched by another tab) gets 204 from `/dt/side`,
`/dt/tabs` and every POST (the POST's store change, audit and event still happen; no strip, no `X-Dt-Href`):
the fragments are dt-only templates. `/dt/tabs.json` answers in any theme. Ids validated (agent id / tmux `$id`); bad input = 400.
Store: `state_dir/v7-tabs.json` = `{"rev", "tabs": [{kind, id}], "current": {kind, id} | null, "closed": [...]}`;
≤ 30 open tabs, ≤ 20 closed.

## 4. DOM (templates) ↔ JS

`base.html` keeps classic's blocks `title`, `bodyclass`, `content`, `flash`, the `csrf-token` and `htmx-config`
metas, `<main>` as the page column with `#flash` in it. `embed` true → no shell (bare content, as classic).

| Hook | Element | Used by |
|---|---|---|
| `html.side-rail` | set before paint by `js/head.js` from `pm7.look.rail()`: `pm.v7.shell.rail` (`rail` / `open`), or with Settings › Display › Sidebar state "per tab" (`pm.v7.shell.railmode` = `tab`) this tab's entry in `pm.v7.shell.railtab` (`{"<kind>:<id>" \| "home": "rail" \| "open"}`, the newest 40 tabs; a tab without an entry follows `shell.rail`). `pm7.look.rail(v)` stores it (⌘B, Settings): the one place that knows these keys | `sidebar.js`, `settings.js` |
| `html[data-tab]` | the server's current tab (`base.html`): `<kind>:<id>` of the open session tab this page is (`agent:claude-1c70ee`, `term:$3`), else `home`; read before paint for the per-tab sidebar state | `head.js` |
| `html.cmp-open` | Settings › Display › Composer on phones = open (all devices, `cmp_phone` in `/dt/prefs`; `head.js` from the prefs mirror): the session composer stays shown under 700 px (`css/session.css`; else it starts collapsed and `[data-c-cmp]` in the session header line, ⌘L, a focus or a draft shows it, `js/session.js`) | `session.js` |
| `html.side-open`, `html.aside`, `html.aside-drag` | overlay sidebar open (< 900 px), chat aside open (restored before paint by `js/head.js` from `pm.v7.aside.open` at ≥ 900 px, not on `html.x-chatpg` (README A28), with `--aside-pct` from `pm.v7.aside.pct`), aside edge drag running | `sidebar.js`, `aside.js` |
| `html[data-theme]` | light/dark/auto from `pm.v7.shell.mode` (`head.js`) | `keys.js` (mode toggle) |
| `html.no-sess` | Settings › Display › "Sessions list in the sidebar" off (this browser, `pm.v7.side.sess` = `0`; `head.js` before paint, again on change): `css/shell.css` hides `.x-shead`, `.x-sfilter` and `#x-sess`, the foot stays at the bottom; `Mod+P` / `>` still find sessions | `settings.js` (writes), `head.js` |
| `html[data-ktoast]` | Settings › Display › Shortcut helper (`pm.v7.keys.toast`): absent = key chip and action, `action` = no key chip, `off` = no toast for a shortcut. A shortcut toast is `pm7.toast(text, key)` with the key's label (`sidebar.js` Mod+B, `aside.js` Mod+L, `tabs.js`, `project.js`); `layers.js`' toast applies the flag, toasts without a key are always shown | `settings.js` (writes), `head.js`, `layers.js` |
| `html.no-khints` | Settings › Display › Show shortcut hints off (`pm.v7.keys.hints` = `0`): hides `p.p-keys` (`css/table.css`) | `settings.js`, `head.js` |
| `body.x[data-live]` | always (dt opens the one stream) | `live.js` |
| `header.x-top` | restart hooks `[data-rl-ok]` (the core would restart: the account menu's Reload pm form is rendered only then), `[data-rl-unit]`, `[data-rl-need]` / `[data-rl-msg]` / `[data-rl-head]` (`reload.js`); `[data-act=side]`, `.x-brand`, `#x-tabs`, `[data-act=pal]`, `[data-act=tabsheet]`, `.x-bell[data-n-bell]` (menu button, `js/notifs.js`), `.x-launch` (link `/launch`), `details#x-acct` | `keys.js` verbs |
| `nav#x-tabs.x-tabs[role=tablist][data-rev]` | per tab `div.x-tab[role=presentation][data-tab-kind][data-tab-id]` (`.home` first, `.on` = current, `[data-kind]` = agent kind) holding `a[role=tab][href][aria-selected][tabindex]` and, beside the link (never inside), `button.cl[data-act=tabclose]` (none on home); then `button[data-act=tabnew]` | `tabs.js`, `search.js`, `keys.js` |
| `aside#x-side.x-side` | `nav.x-nav a[data-nav=<key>][aria-current]`, `input[data-sfilter]`, `#x-sess` (hx-get `/dt/side`, trigger `pm:agent_state from:document, pm:agent_added from:document, pm:agent_removed from:document`, `hx-sync="this:queue last"`: a refresh never aborts the one in flight, so htmx logs no `htmx:sendAbort`), `.x-sfoot` (phones: the Quick chat row `button.x-sqc[data-q-open]`) | `sidebar.js` |
| `#x-sess` rows | `details.x-sg[open]>summary` + `a.x-sr[data-sess-id][data-sess-kind][data-since][href]` with `.dot.<state>` | `sidebar.js`, `search.js`, `ticker.js` |
| `main#main.x-main` | `div.x-page` holds `#flash` and `{% block content %}`; class `x-cl` from `{% block mainclass %}` (default) marks un-ported classic content, a ported page empties the block (`PARTS.md` §1) | — |
| `aside#x-aside.x-aside` | `[data-aside-edge]` (`role=separator`, resize edge), head `.x-ah` (`[data-aside-dot]` + `[data-aside-state]` `.vh` word, `[data-aside-name]` > `bdi`, `[data-aside-kind]` chip, `[data-act=astab]`, `[data-act=aside]` close), `[data-aside-body]` (`.fr` while it holds `iframe.x-aframe`; `[data-aside-empty]` otherwise) | `aside.js` |
| `nav.x-bnav` | phone bottom nav; count badge `.b` only for needs you (Inbox, `.hot`) and unread alerts | — |
| layers | `dialog#x-pal`, `dialog#x-keys`, `dialog#x-stop`, `dialog#x-dlg`, `div#x-menu`, `div#x-sheet`, `div#x-scrim`, `div#x-toast[role=status]` | `layers.js`, `search.js`, `keys.js` |
| `dialog#l-dlg.l-dlg` | Launch dialog (M3): body = `GET /launch/form` (`_launch_form.html`); openers `[data-l-open]`, `pm7.launch.open`, N, `>` "Launch an agent" (`PARTS.md` §15) | `launch.js`, `launch-prompt.js` |
| `div#x-rl` (in `#main`) | Restart bar (M5): "pm is restarting…" after a successful `POST /service/reload` (pm7.fetch or htmx), then a bounded `/healthz` wait (one `setTimeout` chain, 4 s per call, gives up after 90 s, PARTS §13) and a reload; after a 502/503/504 from `pm7.fetch` or htmx the same wait but only "Reload now". "New code" state: the core's `pm_code` verdict (pm's code is newer than the process; `header.x-top[data-rl-need][data-rl-msg][data-rl-head]` at render, the `pm:pm_code` event later) shows "New code: restart pm to load it" plus the commit subject (textContent) and, with `header.x-top[data-rl-ok]`, a Restart pm button: `pm7.confirm`, then `POST /service/reload` (CSRF in body and header) and, on OK, the restart wait; without it the `systemctl --user restart <data-rl-unit>` text, no button. Never restarts without that click. `reload.js` loads right after `core.js` (also in embed: no "new code" state there) and wraps `pm7.fetch` once, so `core.js` stays the only raw `fetch(` caller; global `pm7.reload` | `reload.js` |
| `dialog#sp-dlg.sp-dlg` | Settings popup (M5, README A23): head (title, "Open as page" `a[data-sp-page]` = `/settings?s=<section shown>`, × `[data-act=close]`), body `[data-sp-body]` = `GET /settings/panel?s=<id>` (the dt `_settings_panel.html`, the page's body); openers `[data-sp-open]` (sidebar foot Settings, not on `/settings`), ⌘, (`keys.js` row `settings`: toggle, a layer key), `>` "Settings", `pm7.settings.open(id)`. Not rendered on `/settings` (block `settings_popup` emptied: no duplicate ids) nor in embed | `settings-popup.js`, `settings.js`, `settings-forms.js`, `settings-servers.js` |
| `section#q-panel.q-pop` | Quick chat panel (M3): a soft layer (rank 30), no scrim; openers `[data-q-open]` (top-bar `.x-qc` after the brand, phone drawer `.x-sqc`), Q, `>` "Quick chat" (`PARTS.md` §15) | `qchat.js`, `qchat-menus.js`, `qstream.js` |
| `[hx-confirm]` | on shell markup (outside `main.x-cl`, e.g. Reload pm): `layers.js` answers `htmx:confirm` (capture phase on document, stopped there) with `pm7.confirm`, then `issueRequest(true)`; inside `main.x-cl` classic `dialog.js` keeps it | `layers.js` |
| `[data-act=<verb>]` | shell verbs: side, pal, keys, mode, tabsheet, tabclose, tabnew, aside, close, stop (opens `dialog#x-stop`; closed after its htmx POST to `/panic` succeeds); `aside` and `astab` (Open as tab) are taken by `aside.js` in the capture phase and stopped | `keys.js` dispatch, `aside.js` |

Markup details the JS relies on: session rows carry the id in `.nm` (and `data-label`), the window name in
`data-title` (optional), the age in `[data-age]` with `data-since` on the row, groups `data-project`; the current
tab's `div.x-tab` has `.on` and its link `aria-selected="true"` + `tabindex="0"` (all other tab links `-1`: roving
tabindex), the dot class is the state word; ←/→/Home/End on the strip only move focus (`tabs.js`, capture phase,
stopped so classic `tabs.js`, which binds every `[role=tablist]`, never clicks a dt tab); `kbd[data-k="mod+B"]` labels are filled by JS (leave empty); the keys sheet body is
`[data-keys-body]`; `#x-dlg`, `#x-menu`, `#x-sheet` contents are built by `layers.js`; the toast shows with `.on`
(`.err` for errors); the icon sprite URL is read from the first `<svg><use href>` on the page.

JS: `window.pm7` only (`core.js` first). Storage keys `pm.v7.<area>.<name>`. Events: `live.js` re-dispatches
stream events as `pm:<type>` (classic types, for un-ported page content) and `ui` as `pm7:ui`; `tabs.js` re-fetches
`/dt/tabs` on `pm7:ui` with a newer `rev`; after a stream reopen `live.js` fires `pm7:resync` and the tabs and
session list are fetched again. Every `/dt` fetch sends `HX-Current-URL`.

### Chat aside (`js/aside.js`, M2)

One agent chat (or a read-only terminal) beside any page, as a same-origin iframe of its embed page:
`/a/<id>?embed=1` or `/term/s<n>?embed=1` (the core sends embed pages with `frame-ancestors 'self'`; `base.html`
renders them without the shell). Docked column from 900 px (`--aside-w` = `clamp(360px, --aside-pct, 50vw)`),
fixed overlay `min(440px, 100vw)` with the scrim under 900 px, not used on phones (< 700 px: opening a chat opens its
tab instead). Not used on a session's own page either (`html.x-chatpg`, README A28): no restore, no top-bar
toggle, `open` opens the session's tab, toggle / ⌘L toast; btw still works.

| API (`window.pm7.aside`) | Does |
|---|---|
| `open(id, {focus})` -> bool | shows / switches the aside; `id` = agent id or tmux `$<n>` (terminal); `focus` (default true) puts the cursor into the chat's composer (`textarea[data-cmp-input]`, later `textarea[data-k-input]`), same-origin access after the iframe loaded; phone: `pm7.tabs.open` instead |
| `close()`, `toggle()` | toggle with nothing to show opens the empty aside (it says how to fill it) |
| `current()` -> id \| null | the id shown, null when closed |
| `fromTab()` | ⌘⇧L: the current session tab moves into the aside (stores it, then `pm7.tabs.close(kind, id, {home: true})`, which goes to the home tab, whose page restores the aside) |
| `toTab()` | head "Open as tab": `pm7.tabs.open` + close (not for btw) |
| `btw(id, question?)` -> bool | README A9, PRD §6.3: the aside shows `js/btw.js`'s side-question panel for agent `id` (`pm7.btw.panel(id)`, one per agent, kept for the page's life): head "btw · <id>", chip "no tools", no state dot, Open as tab off; `question` is asked at once, else the question box gets the cursor. Not stored (`pm.v7.aside.*` untouched: the next page restores the last chat, not btw). Phones: a toast (no aside; Quick chat is not ported yet). Callers: the session ⋯ item "btw" (`c:btw`), the composer's btw icon (its text is the question) and `/btw <question>` in the composer (`session.js`, capture-phase submit: never posted to the agent); from inside the aside's iframe `session.js` calls the parent shell's `pm7.aside` |

- btw panel (`js/btw.js`, `css/aside.css` `.x-btw*`): a note ("Nothing is sent to the agent"), the answers, a question
  box (Enter asks, Shift+Enter new line). It posts Quick chat's `POST /qchat/send` (`text`, `model` "", `profile`
  "none" = no tools / MCP / skills, `agent=<id>` on the first turn, `conv=<id>` after) and reads the SSE answer from
  the fetch body (`conv`, `delta`, `done` with the server's escaped-markdown `html`, `error`). Under each answer:
  **Use in composer** (the agent's composer on this page, `[data-composer=live|builder] [data-cmp-form][action=/a/<id>/message]`,
  at the cursor via classic `pm.composer.insert`, not sent) and **Copy**. The conversation also lands in Quick chat's
  history ("btw: <id>"). Errors toast / show the server's message (`pm7.why`).
- `.md` links in the aside chat: classic `chat-md.js` posts `{pm: 'drawer', act: 'md', id, path}` from the iframe;
  `aside.js` (same origin, that iframe only) opens the file in this page's reader when the page is that agent's session
  (`#chatsplit[data-agent=<id>]`: a click on a hidden `a[data-md]` there, which classic `chat-md.js` handles), else in
  a sheet (`pm7.sheet`) with `GET /a/<id>/md?path=` (the server's rendered, escaped markdown). Links between files
  inside that sheet do not follow yet.
- ⌘L on a session page (`#main .c-sess` with a composer) while the aside is closed: the page's own composer gets the
  cursor (again: it leaves it), as the mock does (README A9). Otherwise:
- ⌘L (`pm7.keys.register`, group Panels): open on the focused list row's agent (`data-agent`, else `data-href`
  `/a/<id>`, else `data-row-id` in `[data-list=inbox]`), else the current session row (`#x-sess a.x-sr.cur`), else a
  `.sel` list row, else `pm.v7.aside.last`, with the cursor in its composer. While open: a focused row of another
  agent switches, else the composer (btw: its question box) gets the cursor, else (or from inside the aside) it closes. ⌘⇧L: `fromTab()` on a
  session tab, else `toTab()`. ←/→ on the focused edge: ±1 %.
- Resize edge: drag 20–50 % of the window, stored as `pm.v7.aside.pct` and set with `style.setProperty('--aside-pct',
  'N vw')` on `<html>`; double-click resets to 30 % (key removed); hidden under 900 px.
- Esc: a soft layer at rank 69 (`layers.js`; closes last, DESIGN §7); `keys.js`'s older rank-70 aside layer and its
  `aside` command row are superseded (the row's `run` is pointed at `toggle()`).
- Storage: `pm.v7.aside.open` (`1`/`0`), `pm.v7.aside.last` (id), `pm.v7.aside.pct`. Event `pm7:aside` `{open, id, btw}` on
  document (pages mark the row shown in the aside, `.sel`).
- Head state follows `pm:agent_state` / `pm:agent_removed` and `#x-sess` swaps (state and kind from the session row,
  else the tab, else the id prefix).
- Embed bridge: `aside.js` is loaded in embed pages too; only inside the aside's own iframe (`window.frameElement`
  within `#x-aside`) it registers a soft layer at rank 90 that posts `{pm7: 'esc'}` (Esc after leaving the composer
  closes the aside) and forwards ⌘L ⌘⇧L ⌘K ⌘P ⌘⇧P ⌘B ⌘, as `{pm7: 'key', spec}` (`keys.js` is not loaded there).
  `postMessage` targets `location.origin`; the parent accepts only its own origin and that iframe's window.

## 5. Classic content inside the shell (V7-ISO-10)

`shell/_classic.html` is the one include that loads classic CSS/JS for pages not ported yet. It never loads classic
shell scripts (`side*.js`, `drawer*.js`, `jumprail.js`, `palette.js`, `search.js`, `acct.js`, `qchat*.js`,
`more.js`, `reload.js`, `theme.js`) nor classic `live.js` (dt's `live.js` is the only stream and keeps classic's
refresh behaviour for `#inbox`, `#chat`, `#pane`, `#proj-live`).
