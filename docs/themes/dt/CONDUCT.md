# pm v7: agent conduct (strict rules)

For every contributor and coding agent that touches v7 (the `dt` theme): the docs or the code.
Each rule has an id. Cite the id in reviews, commits and review findings ("breaks V7-ISO-3").
**MUST** and **MUST NOT** are not negotiable without a dated user decision. **SHOULD** needs a reason in the
commit message when you do not follow it.

`AGENTS.md` "Hard rules" (security, tmux, git, tokens) apply on top of everything here and win on conflict.

## 1. Know which version you are in (V7-ID)

- **V7-ID-1** MUST decide before the first edit whether the task is **current pm** or **v7**. If the user did
  not say and the files do not say v7, it is current pm. If it is still unclear, ask.
- **V7-ID-2** MUST say which version in the first line of the plan, the commit subject (`v7:` prefix for v7
  work, e.g. `v7 mock: ...`, `v7 app: ...`, `v7 docs: ...`) and the CHANGELOG title.
- **V7-ID-3** MUST NOT use the current pm's UI files (`docs/UI-RULES.md`, `docs/UI-CONDUCT.md`,
  `docs/UI-GLOSSARY.md`, classic's mocks) as the reference for v7 work, nor the v7 files for current-pm work.

## 2. Isolation (V7-ISO)

User decision 2026-10-14: v7 is kept apart from the current pm and its mock lives in its own folder. Since
2026-10-15 "apart" means **a theme**, not a process: v7 is the `dt` theme, served by the same pm process and chosen
per browser (cookie `pm_ui`; user decision 2026-10-15). Since 2026-10-16 it ships in polymux as a first-party
plugin. The ids below keep their numbers; their content follows the theme model.

- **V7-ISO-1** v7 mock files live only in the v7 mock folder. MUST NOT add v7 files to a classic mock folder,
  and MUST NOT edit classic's mocks for v7 work.
- **V7-ISO-2** v7 app code lives only here: templates, static files and `theme.toml` in
  `src/pm/web/themes/dt/`; Python (routes, view models, stores) in `src/pm/web/dt/`; tests in `tests/web/dt/`.
  It is wired as a first-party plugin through the normal plugin API: polymux's own entry point
  `pm.plugins: dt = "pm.web.dt:plugin"`, so the plugin loader's guards (guarded POST routes, `dt_` prefix on
  template globals) apply to it as to any plugin. No `src/pm/v7`, no second service, no second proxy site.
- **V7-ISO-3** Core code never imports `pm.web.dt`; only the entry point names it. `pm.web.dt` imports only the
  plugin API and shared web glue (`pm.web.plugins`, `pm.web.themes`, `pm.web.render`, `pm.web.deps`,
  `pm.security.guard`) and the domain packages (`pm.core`, `pm.agents`, `pm.projects`, ...); never a classic route
  module (`pm.web.routes.*`) or a private (`_name`) helper of a web module. Theme-specific logic never goes into a
  core route or the classic theme. Private or personal features (one host's links, one person's workflow) go into
  a separate private plugin, not into `pm.web.dt`.
- **V7-ISO-4** v7 work MUST NOT change the classic UI: no edits to `src/pm/web/themes/classic/templates/**` or
  `src/pm/static/**` in polymux, nor `docs/UI-RULES.md`, `docs/UI-CONDUCT.md`, `docs/UI-GLOSSARY.md`. Exceptions:
  1. a **theme-neutral core change** both UIs need (per-request theme, a view-model field a route hands every
     theme, a domain function, a plugin hook), made as its own commit with the tests green and classic
     pixel-identical (uishot before/after);
  2. moving a helper out of a classic route module into a domain package or `pm.web.render` so `dt` can use it:
     a pure move, both callers updated, no behaviour change;
  3. the one-line "this file is the current pm; v7 is in `docs/themes/dt`" pointers, and the core's UI theme
     picker.
- **V7-ISO-5** MUST NOT copy a domain or web-glue helper into `pm.web.dt`. If v7 needs it and it is private to a
  classic module, move it (V7-ISO-4 exception 2). Copying classic static files into `themes/dt/static` is allowed
  only as a starting point that is then owned by dt (renamed to v7 names, never kept in sync by hand).
- **V7-ISO-6** v7 MUST NOT share browser state with the current pm: storage keys are `pm.v7.*`, the JS namespace is
  `window.pm7`, and v7 never reads `pm.v5.*` or classic `pm-*` keys. The login session cookie is shared (one
  process, one origin); the UI choice is the `pm_ui` cookie and nothing else.
- **V7-ISO-7** The `dt` theme and `pm.web.dt` start no background loops. The core's loops (ntfy, alerts,
  autoexit, delivery, chat feed, backup, watchdog) run once, in the core, for both UIs.
- **V7-ISO-8** v7 shares the user's data with the current pm (same `state_dir`, `pm.db`, audit log, prefs). Every
  write MUST go through the same domain function and lock the classic UI uses (`JsonStore`, `filelock`,
  `pm.core.db`). Never a second writer path, never a v7-only copy of shared state.
- **V7-ISO-9** v7-only state (open tabs, pins, Display prefs) lives in a v7-named store (table or JSON file with
  `v7` in the name) through `pm.core` helpers, written only by `pm.web.dt`. Classic never reads it.
- **V7-ISO-10** A page not ported yet renders its classic page template inside the dt shell. The dt shell loads
  the classic CSS/JS that this content needs only through one include (`themes/dt/templates/shell/_classic.html`),
  which shrinks as pages are ported and is deleted at cutover. dt's own files never depend on classic classes.

## 3. Security (V7-SEC), same bar as the current pm

- **V7-SEC-1** Every mutation: POST + CSRF + Origin + logged-in session + tier + audit line, via
  `pm.security.guard.mutation_guard` / `csrf_session_guard`. Fail closed if the audit write fails. No GET
  mutation, ever. A route-guard test for the dt routes (`tests/web/dt/test_route_guards.py`, like the core's
  `tests/test_route_guards.py`) MUST exist before the first POST route; the plugin loader also refuses them.
- **V7-SEC-2** CSP `script-src 'self'; style-src 'self'`: no inline `<script>`, no `style=` attribute, no
  `<style>`, no event-handler attributes, no `eval`. Values that must be dynamic are set through CSSOM
  (`el.style.setProperty('--v', ...)`) from a static JS file. The mock's inline `style="--cols:..."` was a mock
  shortcut, not allowed in the app.
- **V7-SEC-3** tmux / git / compose / virsh calls: argv list, no shell, timeout, fixed binary, tmux targets by id,
  user values after `--` (`pm.core.proc.run_argv`).
- **V7-SEC-4** Pane and chat text is ANSI-stripped and masked on every frame; never logged, indexed or sent to
  ntfy. The read-only terminal stream is the only raw exception (as in the current pm).
- **V7-SEC-5** Never print, log, screenshot, commit or ntfy pm's token, TOTP or the optional dev token.
  MCP env shows names only, never values; command lines shown as binary + short args.
- **V7-SEC-6** Destructive actions confirm in the page: plain confirm when it can be undone, **typed name**
  when it cannot (delete branch / VM / issue history, kill session or window, discard file changes, undefine,
  force off). The label names the object. `DESIGN.md` §7.
- **V7-SEC-7** The read-only terminal never sends keystrokes. The UI says so and points to the chat or
  `tmux attach`.
- **V7-SEC-8** Builder preview iframe: sandboxed, `connect-src 'none'`, allowed hosts only (`builder_frame_hosts` in
  pm.toml and the agent's static files). Same rule as the classic Builder.

## 4. Design (V7-UI)

- **V7-UI-1** Follow `DESIGN.md` and `LAYOUT.md`. A second look for the same thing is a bug: reuse the
  component (`DESIGN.md` §6) or extend it; never a page-local copy.
- **V7-UI-2** Tokens only. Every colour, size, radius, shadow, z-index and font comes from v7 `tokens.css`
  (`DESIGN.md` §3). No hex, `rgb()`, `hsl()` or raw `z-index` in a component file. No custom property on
  `:root` outside `tokens.css`.
- **V7-UI-3** Colour means state, action or agent kind; never decoration. Shadows only on floating layers,
  from the shadow tokens `--shadow-menu`, `--shadow-dlg`, `--shadow-side`; scrims from `--scrim` (never mixed
  from `--fg`, which turns light in dark mode).
- **V7-UI-4** No native popups (`alert`, `confirm`, `prompt`, `window.open` popups). One layer system
  (`DESIGN.md` §7): dialogs, menus, sheets, toasts, Esc order. Never a second backdrop, Esc or outside-click
  handler in a page file.
- **V7-UI-5** Design changes (a token, a component's look, a rule in `DESIGN.md` / `LAYOUT.md`) need an explicit
  user yes first. A mock or a screenshot is not a yes. Apply it to every v7 page in the same change and update
  the doc in the same commit.
- **V7-UI-6** Every interactive element: keyboard reachable, `:focus-visible` ring, `aria-label` + `title` on
  icon-only controls, state never by colour alone (dot + `title` + screen-reader word).
- **V7-UI-7** Every shortcut is listed in the shortcuts sheet (`?`) and in `DESIGN.md` §8. A new key needs a
  browser-conflict check (Chrome, Firefox, Safari) written into that table.

## 5. Code (V7-CODE)

- **V7-CODE-1** `docs/ARCHITECTURE.md` applies to v7 Python: layers, shared helpers, size limits (module ≤ 600,
  function ≤ 80, route ≤ 40 lines), naming, no `subprocess` outside `run_argv`, no bare threads.
- **V7-CODE-2** Frontend limits: JS file ≤ 500 lines, CSS file ≤ 500 lines, **line length ≤ 120** (the mock's
  1000-character lines are not allowed in the app). Split by purpose (V7-CODE-6).
- **V7-CODE-3** Server renders the page: Jinja templates + HTMX swaps + small static JS. No JS framework, no
  build step, no CDN, vendored libraries only. JS never decides which page or row is current on first paint.
- **V7-CODE-4** Data is keyed by real ids (agent id, project name, tmux `$`/`@`/`%` ids, issue number), never
  by array index.
- **V7-CODE-5** Toasts that report a result appear only after the server answered OK; on failure show the error
  (masked, no argv, no secrets).
- **V7-CODE-6** One helper per job: fetch/CSRF/escape (v7 `core.js`), layers (v7 `layers.js`), sortable table,
  filter box, ticker, toast. `PARTS.md` §10 lists the JS globals. Never a second copy in a page file.
- **V7-CODE-7** Live data comes from one event stream per page (SSE) and one shared ticker for clocks. No
  per-page `setInterval` polling, no document-wide `MutationObserver`.

## 6. Process gates (V7-GATE)

Run these in order. A failed gate stops the work; report it, do not skip it.

| Gate | Mock work | App work |
|---|---|---|
| **V7-GATE-1** Plan | name the version, the files you will own, the rule ids that apply | same + check the plan against `docs/ARCHITECTURE.md` (package, shared helpers, limits) |
| **V7-GATE-2** Worktree | not needed (static files) | Python, routes, config: a git worktree or branch; ask before merging to main and restarting pm |
| **V7-GATE-3** Static checks | no hex / raw z-index / inline style in new CSS; files ≤ 500 lines | `./tools/check.sh` (it includes the dt static tree) and `uv run pytest` green (dt file rules: `tests/web/dt/test_theme_static.py`) |
| **V7-GATE-4** Visual | Playwright on the mock: 1512, 2560, 3440, 440 (WebKit iPhone), light and dark, read every screenshot | `tools/uishot.py --ui dt` on the dev instance (`tools/devpm.py`), same screens; classic unchanged; read every screenshot |
| **V7-GATE-5** Review | re-read `DESIGN.md` §6 for the components you touched | the diff checked against `docs/ARCHITECTURE.md`; no commit on a failure |
| **V7-GATE-6** Docs | update the mock's decision and page notes and the matching `docs/themes/dt/*.md` | `PRD.md` acceptance ticked, `FEATURES.md` entry (area prefixed `v7`), CHANGELOG entry |
| **V7-GATE-7** Finish | commit (`v7 ...:`), push (never force), check the open issues | same |

- **V7-GATE-8** Never type into a live `/a/<agent>` composer or press mutating buttons on live pages
  (a running pm, either theme) unless the user asked for that action. Use the mock or the dev instance.
- **V7-GATE-9** "Tests pass" is not done. Done = the acceptance list in `PRD.md` for that page is met on every
  screen in V7-GATE-4, and the docs say what was built.

## 7. Forbidden (quick list)

- Editing current-pm UI files for a v7 task, or v7 files for a current-pm task (V7-ID-3, V7-ISO-4).
- New v7 files outside the v7 mock folder, `docs/themes/dt`, `src/pm/web/themes/dt`, `src/pm/web/dt`,
  `tests/web/dt` (V7-ISO-1, -2).
- Core code importing `pm.web.dt`; `pm.web.dt` importing a classic route module; personal features in
  `pm.web.dt` (V7-ISO-3).
- A dt background loop (V7-ISO-7); theme-specific logic in a core route (V7-ISO-4).
- Hex colours, raw z-index, raw px sizes outside the token table, `:root` variables outside tokens, shadows or
  scrims mixed from `--fg` (V7-UI-2, -3).
- Inline script or style, native popups, GET mutations (V7-SEC-1, -2, V7-UI-4).
- A second modal, menu, toast, filter, sort or Esc implementation (V7-UI-1, V7-CODE-6).
- Data addressed by array index; "done" toasts before the server answered (V7-CODE-4, -5).
- Changing a token, a component look or a rule without a user yes (V7-UI-5).
