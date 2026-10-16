# pm architecture rules

Where code goes, which shared helper to use, and the limits that keep the tree clean. The `arch-guard`
sub-agent (`.claude/agents/arch-guard.md`) checks every code change against this file **before** and
**after** it is written. Other agents (Codex, Devin, Cursor) follow it by hand and run `./tools/check.sh`.

The flat modules were moved into the packages below in Phase 3 (2026-10-12, a pure scripted move). Put new
code in the package that owns the domain, never in a new top-level module.

## Target layout

| Package | Owns | Modules (moved 2026-10-12) |
|---|---|---|
| `pm.core` | config, models, executors, subprocess (`run_argv`), file IO (`atomic_write`, `JsonStore`, `safe_join`, flock/leases), caches (`LRU`, `SWR`), time formatting, logging setup, /proc sampling (`procstat`), chat upload names and storage (`uploads`), the pm.db SQLite helper (`db`: `connect`, used by the chat stores and the tracker), Markdown rendering, code highlight, prefs | `errors`, `proc`, `fsutil`, `config`, `models`, `executors`, `filelock`, `cache`, `timefmt`, `markdown_render`, `codehl`, `prefs`, `db` (moved from `pm.chat.db` 2026-10-13) |
| `pm.security` | auth, sessions, passkey, CSRF + Origin, `mutation_guard`, audit, masking | already a package |
| `pm.tmux` | tmux control, kill, PTY terminal | `ctl`, `kill`, `term` |
| `pm.git` | git calls, remotes | `ctl` |
| `pm.agents` | running agents (registry, detect, launch, message, fork, stop, resume, slash, state), header, sub-agents, autoexit, usage, limits, inbox, recap, labels, sorting; `inventory/` = installed agents' skills/MCP/scripts (`read`, `mutate`) | `head`, `subagents`, `autoexit`, `usage`, `limits`, `inbox`, `recap`, `labels`, `sorting`, `inventory/` (`read`, `specs`, `fsread`, `mcp`, `skills`, `rules`, `scripts`, `mutate`, `mutate_base`, `mutate_skills`, `mutate_rules`, `validate`) (+ the existing `registry`, `detect`, ...) |
| `pm.chat` | live chat view, stored chat log, stats, text/markdown of chats, `sources/` | `live`, `log`, `stats`, `text`, `sources/` |
| `pm.qchat` | quick chat runner, send preparation, settings, MCP config, skills, db | `run`, `send`, `cfg`, `mcpcfg`, `skills`, `db` |
| `pm.infra` | servers, server store, libvirt, ssh keys, compose stacks, host resources, process control, snapshot | `servers` (+ `servers_model`, `hostmetrics`), `unitctl` (pm unit restart), `servers_store`, `libvirt`, `sshkeys`, `stacks`, `resources`, `procctl`, `snapshot` |
| `pm.projects` | projects, BACKLOG/TODO (file mode), plans, project files, canvas; `tracker/` = BACKLOG/TODO/CHANGELOG/FEATURES in pm.db for imported projects (store, import/render, issues, views, branch drafts, git glue, the `pmctl` CLI) | `projects`, `backlog`, `todo`, `plans`, `projfiles`, `canvas`, `tracker/` (`schema`, `store`, `issues`, `mdimport`, `mdgen`, `views`, `resolve`, `gitglue`, `cli`, `cli_cmds`; T3: `changelog`, `featurelist` + `featurestore`, `docsync` (shared change/sync skeleton of the two), `drafts` (branch views, ancestor checks, post-commit stamps; the only T3 module that calls `pm.git`), `cli_docs`) |
| `pm.notify` | event hub, alerts, ntfy publish/feed/owner, watchdog, reload notice, client errors, side badges | `events`, `alerts`, `ntfy`, `feed`, `owner`, `watchdog`, `reloadneed`, `clienterrors`, `sidebadges` |
| `pm.web` | app factory, deps, render, assets, stats, `routes/` | `app`, `assets`, `stats`, `deps`, `render`, `listing`, `docpane`, ... , `routes/` |
| `pm.web.dt` | the `dt` theme's Python (pm v7): routes (prefix `/dt` and overrides of core pages), view models, v7-only stores; a first-party plugin, templates and static files in `src/pm/web/themes/dt` | `routes`, `shell`, `menus`, `stores`, `prefs`, `inbox`, `session`, `ntfy`, `agents*`, `projects`, `project`, `pjops`, `files`, `backlog`, `tmux`, `servers*`, `audit*`, `settings`, `launch` |

Themes and plugins: `pm.web.themes` is the theme registry (`classic` = today's UI, built in and the fallback for
every template another theme does not override); `pm.web.plugins` loads plugins from the entry-point group
`pm.plugins` and adds their routers, nav items, Settings sections, background loops, themes, template and static
dirs at app build (`plugins.install` from `create_app`).

polymux ships two themes. `classic` is built in. `dt` (pm v7, keyboard-driven shell) is a first-party plugin:
Python in `pm.web.dt`, templates, static files and `theme.toml` in `src/pm/web/themes/dt`, registered through
polymux's own entry point (`pm.plugins: dt = "pm.web.dt:plugin"` in `pyproject.toml`). It goes through the same
loader as any plugin, so its routes pass the guard checks and its template globals must carry the `dt_` prefix.
Core modules never import `pm.web.dt`; `pm.web.dt` never imports `pm.web.routes.*`. Its rules are
`docs/themes/dt/CONDUCT.md`. Module names in `pm.web.dt` (`agents`, `inbox`, `settings`, `projects`, ...) repeat
core names on purpose: same concept, theme layer; import them by full path (`pm.web.dt.inbox`). No
`pm.v7` / `pm.webcore` package. Private or personal features go into a separate plugin, not into `pm.web.dt`.

A plugin's mutating routes use `mutation_guard` (with a tier) like core ones; the loader skips a plugin with an
unguarded mutating route, a non-GET plain route, a Mount or a websocket. Link hooks (`project_links`,
`agent_cwd_url`, `agent_transcript_url`) are called per request through `plugins.project_link_groups` /
`plugins.first_url`, never directly from routes; only http(s) URLs pass. A plugin that fails to load is logged and
skipped. Plugins are trusted code (they run in pm's process and get `app` and `settings`): the guard check catches
mistakes, not malice.

## Layer rules

1. Imports flow one way: `web` → domain packages → `core`. `security` imports only `core`.
2. Domain code never imports `fastapi`, `starlette`, `jinja2` or `pm.web`. Raise a domain error
   (`pm.core.errors`: `Invalid`, `Denied`, `NotFound`, `Conflict`), and let the route map it to HTTP.
3. Route handlers are thin: parse input, call one domain function, render. No subprocess, no file IO
   beyond a template, no business logic over ~40 lines.
4. No import cycles, not even ones that "work" through local imports (archcheck `cycle:`).
6. Package dependencies (archcheck `pkg-edge:`, function-local and `TYPE_CHECKING` imports count; never
   baselinable). A package may import only:

   | Package | May import |
   |---|---|
   | `core` | nothing in pm |
   | `security`, `tmux`, `git` | `core` |
   | `chat` | `core`, `security`, `tmux` |
   | `projects` | `core`, `security`, `git` |
   | `agents` | `core`, `security`, `tmux`, `git`, `chat`, `projects` |
   | `qchat` | `core`, `security`, `chat`, `projects`, `agents` |
   | `infra` | `core`, `security`, `tmux`, `git`, `projects`, `agents` |
   | `notify` | `core`, `security`, `tmux`, `projects`, `agents`, `infra` |
   | `web` | everything |

   archcheck reads static imports only: never use `importlib.import_module`/`__import__` to get around it.
   Loading code by entry point (`importlib.metadata.entry_points`) is allowed only in `pm.web.plugins`, and only
   for plugins, never for pm's own packages. The one first-party plugin, `pm.web.dt`, is loaded this way on
   purpose, so it passes the same loader guards as an external one.
   Need a higher package from a lower one? Inject it (pass a function from `web/app.py` or the route),
   as `autoexit.start(app, published)` and `servers.get_panel(..., ntfy_fn=)` do.
5. Background work (loops, refresh threads) is started from the app lifespan or through `SWR`;
   never a bare `threading.Thread` or `asyncio.create_task` at import time or from a route.

## Shared helpers: use these, never write another copy

| Need | Use | Never |
|---|---|---|
| Run a program | `pm.core.proc.run_argv(argv, timeout=...)` (raises `ProcTimeout`/`ProcNotFound`/`ProcError`); fire-and-forget: `spawn_detached(argv)` (raises `ProcNotFound`/`ProcError`) | `subprocess.*` in a new place, `shell=True`, a bare binary without a config/fixed path, user values before `--` |
| Write a file | `pm.core.fsutil.atomic_write(path, data, mode=, fsync=)`; JSON state: `pm.core.fsutil.JsonStore` | another tmp + `os.replace` copy |
| User-supplied path | `pm.core.fsutil.safe_join(root, rel)` (no `..`, no symlink escape) | ad-hoc `realpath` checks |
| Lock a shared file | `pm.core.filelock.locked()` / leases | raw `fcntl.flock` |
| Cache | `pm.core.cache.LRU` (bounded) or `SWR` (background refresh) | a module-level dict without a size limit |
| Time/duration text | `pm.core.timefmt` / `web.render.ago` | another `human()` |
| Pane text out of the process | `security.mask.render_pane` / `mask` | sending raw pane text (except the P7 terminal stream) |
| Mutating route | `Depends(mutation_guard(action, tier))` + `audit_result`; special cases (step-up, header-only CSRF): `csrf_session_guard(...)` | hand-copied session/Origin/CSRF checks |
| Thread pool from async | `pm.core.executors.run_*` | blocking IO or subprocess inside `async def` |
| Setting | a typed field in `pm.core.config.Settings` | `settings.extras.get("new_key")` |
| Origin check | `security.csrf.origin_ok` (via `mutation_guard` / `Csrf.origin_ok`) | another Origin parser |
| JS fetch / CSRF / escape | `pm.fetch(url, {csrf: 'header'\|'body'\|'both'})`, `pm.csrf()`, `pm.esc`, `pm.debounce` from `static/js/core.js` (loaded first) | raw `fetch(`, another CSRF lookup or `esc()` (archcheck `js-fetch`, `js-csrf`). Document-level click handlers stay per feature: several rely on capture-phase order and `stopImmediatePropagation` (`rowx.js`, `dialog.js`), so never merge them blindly |
| Nav entry | `render.NAV`, the one nav list (`NavItem`); plugins append theirs at app build (`nav_items()` hook) | a second nav list in a template or route |
| Colour / design token | a token from `static/css/tokens.css` (the only `:root` custom properties; archcheck `css-root-token`). A theme other than classic carries its own `tokens.css` in its own static dir (`/static/t/<name>/`) | hex/rgb literals in a component file (archcheck `css-color` counts may only shrink) |

## Size limits

- Module ≤ 600 lines, function ≤ 80 lines, route handler ≤ 40 lines, JS file ≤ 500 lines, CSS file ≤ 500 lines.
- Existing offenders are listed in `tools/archcheck_baseline.json` (`entries`, plus per-file process-spawn
  `counts`). Both may only shrink: never add an entry or raise a count. If a change pushes a file over a
  limit, split it in the same change. `shell=` anything but `False` is always an error.

## Naming

- Name a module after what it does (`snapshot.py`), not how it was built (`v4-lists.css`, `test_chat_r5.py`).
- No two modules with the same name in different packages unless they are the same concept on different layers.
- A test file is named after the module it tests: `tests/<package>/test_<module>.py`.

## Every change also

- adds or updates tests (`uv run pytest` green),
- passes `tools/check.sh` (= `make check`: ruff + `tools/archcheck.py`),
- updates `CHANGELOG.md` when behaviour changes and `FEATURES.md` for user-visible ones,
- follows `docs/UI-RULES.md` for UI and the hard rules in `AGENTS.md`.

## Migration status

| Phase | What | Status |
|---|---|---|
| 1 | Stabilise: fix red tests, ruff, `tools/archcheck.py` + baseline, `make check`, logging setup, route-guard test, `atfiles` containment, git timeout | done 2026-10-12 |
| 2 | Shared helpers in `pm.core` (`run_argv`, `atomic_write`, `JsonStore`, `safe_join`, errors), replace copies, typed `extras` | done 2026-10-12 |
| 3 | Move modules into the packages above, break cycles, import contracts | done 2026-10-12 |
| 4 | Split long functions, move logic out of routes; one `csrf_session_guard`; route handlers ≤ 40 lines (archcheck `route-lines`) | done 2026-10-12 |
| 5 | Frontend: one `tokens.css`, CSS files named by purpose (`base`, `chrome`, `rows`, `data`, `agent`, `shell`, `icons`, `nav`, ...), `core.js`, `chat.js`/`qchat.js` split, `tools/uidiff.py` (pixel + computed-style diff vs main) | done 2026-10-12 |
| 6 | Docs and repo: split CHANGELOG, archive stale docs and old mocks, rename tests, shared test fixtures | done 2026-10-12 (left for the user: old mocks v0–v3, merged gitdev branches, CHANGELOG fragments) |
