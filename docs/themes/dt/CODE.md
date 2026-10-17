# dt theme: code layout, one helper per job, limits

Where dt code lives, which helper does each frontend job, and the size and style limits. The rules behind it are
`CONDUCT.md` (V7-ISO, V7-CODE); the shell contract is `SHELL.md`; the page parts (macros, data hooks, JS APIs) are
`PARTS.md`. `docs/ARCHITECTURE.md` applies to the Python. `./tools/check.sh` (= `make check`) and
`tests/web/dt/test_theme_static.py` check the static rules.

dt is a theme plus a first-party plugin, not a process: the plugin is loaded through the entry point
`pm.plugins: dt = "pm.web.dt:plugin"` (class `DtTheme` in `src/pm/web/dt/__init__.py`).

## 1. Tree

```
src/pm/web/
  dt/                 dt Python (no pm.web.routes imports, V7-ISO-3)
    __init__.py       the plugin object DtTheme: themes() -> dt, routers() -> dt routes, template_globals()
    shell.py          shell view model: nav rows with counts, sessions grouped by project, tabs (Jinja global dt_shell)
    stores.py         v7-only state: the open tabs (pm.core JsonStore, state_dir/v7-tabs.json)
    prefs.py          v7 "all devices" prefs: Settings › Display, Chat colours (state_dir/v7-prefs.json, a whitelist
                      of keys with enum / bool / #rrggbb values; GET/POST /dt/prefs)
    routes.py         /dt/... routes: tabs API (+ close-all), sidebar partial, prefs (every POST behind mutation_guard)
    menus.py          the one ⋯ menu-item builder (PARTS.md §9) used by every view model
    inbox.py ntfy.py session.py   page view models (Jinja globals dt_inbox, dt_ntfy, dt_session): lists, URL
                      state, row classes, detail facts; templates only loop over them
    settings.py       the Settings page's view model (dt_settings): sections, rows, ⋯ items, read-only lines
    agents.py agents_library.py agents_usage.py agents_base.py   the Agents page (dt_agents): Installed and the
                      agent detail / Library and MCP / Usage, limits, Findings / shared plan + column helpers
    servers.py servers_res.py   the Servers page (dt_servers): Overview and Services / Resources and VMs
    audit.py audit_errors.py    the Audit page (dt_audit): Activity / Client errors
    backlog.py        the Backlog page and the project view's Issues / TODO tabs
    project.py projects.py files.py pjops.py   the project view, the Projects list and New project, the Files tab,
                      the VMs tab
    tmux.py           the tmux page
    launch.py         the Launch dialog's form
  themes/dt/
    theme.toml        name, label, description
    templates/
      base.html       the dt shell (top bar, sidebar, tab strip, main, aside, layers root, toast); classic page
                      templates extend it unchanged until they are ported (V7-ISO-10)
      shell/_top.html  _sidebar.html  _navrow.html  _sessions.html  _tabs.html  _aside.html  _bnav.html  _layers.html
      shell/_classic.html  the classic CSS/JS that un-ported page content still needs (shrinks per page)
      shell/_launch.html  _qchat.html  _settings.html   shell layers (Launch, Quick chat, Settings popup)
      parts/_table.html  _filter.html  _seg.html  _tabs.html  _menu.html  _switch.html  _select.html  _head.html
           _meter.html  _status.html  _agentrow.html   macros shared by pages (PARTS.md)
      <page>.html     a ported page overrides the classic template of the same name; its partials sit beside it
                      (`_name.html`) or in a folder named after the page (`agents/`, `backlog/`, `project/`, ...)
    static/
      css/tokens.css  the only :root and colour-literal file
      css/base.css  shell.css  layers.css  keys.css  parts.css  table.css  ...  <page>.css
      js/head.js      blocking head script (no flash, L-1): sidebar rail, light/dark, the this-browser looks (fonts,
                      sizes, hints) and the mirror of the all-devices prefs (refreshed from /dt/prefs)
      js/core.js      pm7.fetch / why / fail / reply / esc / debounce / store (loaded first)
      js/layers.js    dialogs, confirm, menus, sheets, Esc order, focus return, toast
      js/keys.js      the key table and dispatch (DESIGN.md §8), the shortcuts sheet, G-chords
      js/tabs.js  search.js  sidebar.js  aside.js  btw.js  notifs.js  table.js  filter.js  parts.js  listkeys.js
      js/ticker.js  live.js  reload.js
      js/<page>.js    page scripts (parts contract: PARTS.md)
      icons.svg
tests/web/dt/         test_<module>.py per module (plugin loaded explicitly, fake tmux); kit.py = shared helpers
```

Static files are served at `/static/t/dt/...`. File names inside the theme do **not** repeat `v7-`. CSS classes use
the prefixes in `DESIGN.md` §2. Browser storage keys are `pm.v7.<area>.<name>`; the JS namespace is `window.pm7`.

### 1.1 What lives where

| Need | Where | Note |
|---|---|---|
| Per-request theme (cookie `pm_ui`, `?ui=`), one Jinja env per theme, theme static versioning | `pm.web.render`, `pm.web.themes`, `pm.web.assets` | theme-neutral core (V7-ISO-4 exc. 1) |
| UI theme picker | classic Settings and the dt account menu / Settings | sets the cookie only; no server write |
| Plugin hook `template_globals()` | `pm.web.plugins` | how `dt` gets its view models into templates |
| A page's data | the core route's view-model dict, same for both themes | never theme logic in a route |
| v7-only state: open tabs | `src/pm/web/dt/stores.py` (`v7-tabs.json`) | V7-ISO-9 |
| v7-only state: Display / Chat colours prefs ("all devices") | `src/pm/web/dt/prefs.py` (`v7-prefs.json`, `/dt/prefs`) | V7-ISO-9; launch defaults and ntfy switches stay the core's prefs (V7-ISO-8); per-browser looks are `pm.v7.*` storage applied by `js/head.js` |
| Shell view model (nav counts, sessions list) | `src/pm/web/dt/shell.py`, from domain functions | read-only |
| Page view models, ⋯ items | `src/pm/web/dt/<page>.py`, `menus.py` | read the route's context only; no IO |

Domain packages keep `docs/ARCHITECTURE.md` rules (no framework imports, domain errors, shared helpers).

### 1.2 Process and data

- One pm process. Each request renders with the theme from `?ui=` (a valid name is also remembered in the `pm_ui`
  cookie), else the `pm_ui` cookie, else `theme` in pm.toml, else classic.
- The dt theme starts no loops (V7-ISO-7); the core runs them once for both UIs.
- Same `state_dir`, `pm.db`, audit log and prefs (V7-ISO-8). v7-only state in `stores.py` and `prefs.py` (V7-ISO-9).
- Login, passkeys and the session cookie are the core's; nothing to re-register.
- Live: the core's SSE stream (`/events`), opened once per page by `js/live.js`.

## 2. Frontend helpers: one per job (V7-CODE-6)

| Job | File | Notes |
|---|---|---|
| fetch, CSRF, escape, debounce, storage | `js/core.js` (`pm7.fetch`, `pm7.esc`, `pm7.debounce`, `pm7.store`) | only file with raw `fetch(` and the CSRF lookup (archcheck); `pm7.why(r, what)` / `pm7.fail(what, r)` = a failed answer's server message (JSON `detail`, text body; masked, one line) for error toasts |
| service reload / 5xx wait | `js/reload.js` (`pm7.reload`, wraps `pm7.fetch`) | loads right after `core.js`; one bounded wait (PARTS.md §13) |
| dialogs, confirm, typed confirm | `js/layers.js` (`pm7.dialog`, `pm7.confirm`) | native `<dialog>` + `showModal`; never `alert` / `confirm` / `prompt` (V7-UI-4) |
| menus, sheets | `js/layers.js` (`pm7.menu`, `pm7.sheet`) | one stack, one Esc, one outside click; row ⋯ triggers: `parts/_menu.html` + `js/parts.js` (`pm7.menus`) |
| toast | `js/layers.js` (`pm7.toast`) | after server OK only (V7-CODE-5) |
| keys | `js/keys.js` (`pm7.keys.register`) + `js/listkeys.js` | one table; pages register their keys, list keys J K ↵ O X E P via the list hooks (PARTS.md §7) |
| sortable grid table, row details, folds | `js/table.js` + `parts/_table.html` + `css/table.css` | server-side order with `?sort=` header links, `aria-sort`; subgrid rows (PARTS.md §6) |
| filter box | `js/filter.js` (`pm7.filter`) + `parts/_filter.html` | client hide or debounced `hx-get`; keeps the caret on swap (PARTS.md §5) |
| clocks (running age, working words) | `js/ticker.js` (`pm7.ticker`) | reads `data-since`, one timer (V7-CODE-7) |
| live updates | `js/live.js` | one EventSource; re-dispatches the core's events on `document` |
| tabs and session list | `js/tabs.js` (`pm7.tabs`) | server-stored (`/dt/tabs*`), follows the tabs event |
| search dialog | `js/search.js` | server results, `>` commands |
| Settings body (page and popup) | `js/settings.js` (`pm7.settings.init(root)`, `show`, `post`, `refresh`) + `js/settings-forms.js`, `js/settings-servers.js`; popup frame `js/settings-popup.js` (`pm7.settings.open / close / toggle / isOpen`) | one template (`_settings_panel.html`), never a second copy for the popup |
| composer | classic markup and JS, loaded through `shell/_classic.html` (README A5) | a dt port gets one macro in `parts/` for tab, aside, Builder, Quick chat and btw |
| dynamic values (bar width, column template) | CSSOM `style.setProperty` or classes | never inline `style=` (CSP, V7-SEC-2) |

The full list of JS globals and events: `PARTS.md` §10.

## 3. Size, style and naming limits

- Python: `docs/ARCHITECTURE.md` limits (module ≤ 600, function ≤ 80, route handler ≤ 40 lines).
- JS and CSS: file ≤ 500 lines, line ≤ 120 characters, one area per file (V7-CODE-2). Split a file that grows past
  the limit by purpose (`parts.css`, `table.css`, `<page>.css`, ...).
- Templates: one page per file; partials prefixed `_`; macros in `parts/`. No logic beyond loops and conditions;
  compute in Python (the view models in `src/pm/web/dt`).
- No dead code: remove unused classes and handlers when you touch a file.
- Comments say why, not what. File head comment: what the file owns, its globals, its URL/data hooks.
