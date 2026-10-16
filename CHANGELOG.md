# Changelog

## 2026-10-16 (dt theme ships with polymux)

- The `dt` theme now ships with polymux: a keyboard-driven shell with tabs, a sessions sidebar, command menus and layered panels; vim-style list keys (J/K, G then a letter, `/`, `?`) and macOS-style search and switching (⌘K, ⌘P, ⌘B, ⌘,; Ctrl elsewhere). Pick it per browser in Settings or with `?ui=dt`, or server-wide with `theme = "dt"` in `pm.toml`; classic stays the default. Code: `src/pm/web/dt`, `src/pm/web/themes/dt`; rules and design: `docs/themes/dt/`.
- README: project status, why polymux exists, themes and dt screenshots (desktop and iPhone, light and dark).
- Dev tooling: `tools/uishot.py --engine webkit` logs in again (WebKit dropped the session cookie on the login redirect).

## 2026-10-15 (Agents, Servers, Audit and Settings data and actions for themes (v7 M4 core))

- Agents: review / apply answer JSON to fetch calls (classic forms unchanged); `POST /agents/rescan` drops the inventory and usage caches; `GET /agents/auth.json` gives every agent's login state from the cache (`?fresh=1` re-checks).
- Agents findings: each finding has a kind, an agent and a planned fix; `/agents?t=findings` groups them; new reviewed fixes link missing shared skills, drop stale `agents-index.md` lines and move an MCP key from the args into env (backup first, re-checked just before the write).
- Agents Usage: the Claude plan limits and the session ids of the top sessions.
- Servers: the compose stack of each container and Restart stack (members found by docker, protected containers skipped), Start for stopped containers, stop or kill every process of a program with a typed confirm.
- Settings: replies carry an ok / notice signal (`HX-Trigger pm:notice`, JSON for explicit JSON requests); Revoke all other sessions; Re-pin host key with the typed server name; `GET /settings` carries the panel data; the loaded pm.toml path is known.
- Audit: the filter matches target, client and detail too (all words); Refused and Failed result classes with counts; a category filter; Export CSV (`/audit/export.csv`); the Client errors tab renders on first paint (`?tab=errors`).
- Client errors: reports carry a masked stack, the browser family and CSP violations (kind `csp`, also listed in the classic kind select); a grouped view gives one row per message with its count and pages.
- Fix: client-error messages are masked again (masking had been skipped silently).


- Agents: an applied change clears the inventory cache, so the next page load shows it.
- Audit: a plugin's namespaced action (e.g. `dt.prefs.save`) gets the category of its bare name.
- Audit names: process, program and container actions log the attempt and the result under the same name; Clear client errors writes its audit line before clearing (fails closed); Re-pin host key needs tier 2.

## 2026-10-15 (Projects, Backlog, tmux and git operations for themes (v7 M3 core))

- Issues: move an issue to another project (`POST /p/{n}/issues/{num}/move`, `pmctl issue move`), dispatch one issue to a new agent; `/backlog` carries cross-project issue and TODO lists; the projects list has segment and open-issue counts.
- TODO: add an item to a tracker project from the web (`POST /p/{n}/todo/add`, text and optional section).
- tmux: list filters (agents / attached / protected) with counts, an optional start folder for a new session (under the projects root), pm's own session in the window rows; after a tmux new / rename / kill the lists refresh at once (snapshot rebuild + event).
- Git tab JSON API: detailed status, stage / unstage / discard, commit, stash, checkout, merge, branch delete, pull with stash, each with a "what will happen" plan; typed confirms for the destructive ones.
- Project stack (`/p/{n}/stack`: services, logs, up, down) and project VMs (count on the project page from the cached listing, snapshots, console info, start / shutdown / force off / delete).
- New project dialog: planner (`/p/new/plan`) and step runner (`/p/new/run`, NDJSON) with a plugin step hook; plugin steps run in their own bounded pool.
- Launch form partial `GET /launch/form` for themes.
- Dev tooling: `tools/devpm.py` remembers `--pm-bin`, `--extra-toml` and `--limits`; a restart (also the one uishot triggers) keeps them and the fixture data (#206).

## 2026-10-15 (Inbox and notification data for themes; mark unread; mute titles)

Theme-neutral additions used by the dt theme's Inbox and Notifications (classic looks the same):
- Inbox rows carry ctx %, unread count, error line, last message with its source, model and the pending question's options; sort key `ram`.
- Mark an Inbox item unread (`POST /inbox/mark-unread`); mute a notification title for 24 h (`POST /ntfy/mute`, `/ntfy/unmute`; muted titles drop out of every list and count).
- Live agent events carry unread and the last message; an ntfy for an agent publishes one combined inbox event.
- Notification rows know their day (today / yesterday / earlier), source and "recovered after"; `GET /ntfy/bell` takes `n` and `unread_first`.

## 2026-10-15 (Agents start without shell re-parsing their arguments (#204))

Launch, Fork, Resume, the agent login window and the New Chat respawn start the agent through tmux's default-shell (so fish's config.fish still sets SSH_AUTH_SOCK, mise and PATH) with the argv as positional arguments: no shell re-parses them, and fish no longer misreads sh quoting.
New Chat's typed relaunch in an interactive shell is quoted for that pane's shell (fish or sh/bash/zsh/dash); an argv that cannot be typed is refused before the agent is asked to exit.
Prompts, arguments and working directories ending in ';' arrive intact (tmux no longer reads them as a command separator).

## 2026-10-15 (Dates follow the display time zone (#202))

- Dates pm writes or shows follow the configured display offset (`display_utc_offset_h`) instead of the host timezone: issue/TODO title dates, imported item times, CHANGELOG and FEATURES dates, backup file names, the Servers panel time and the Usage day buckets. Same on a host whose timezone matches the offset.
- `pmctl issue show` prints times with their zone (e.g. `2026-10-15 03:28:45 UTC+7`). (#202)

## 2026-10-15 (New Chat keeps a launched agent's window (#201))

- New Chat on an agent started by Launch no longer closes its tmux window: when the agent has to quit and relaunch (non-Claude kinds, or Claude with an update pending), pm keeps the pane and restarts the agent in it (`respawn-pane` with an argv list). Panes where the agent runs under an interactive shell work as before. (#201)

## 2026-10-15 (Per-browser UI theme)

- Each browser can pick its UI theme: Settings › UI theme (shown when a plugin adds a theme) or `?ui=<name>`, remembered in the cookie `pm_ui`; pm.toml `theme` stays the default. Classic looks unchanged.
- Plugins: new hook `template_globals()` (keys prefixed with the plugin name); theme and plugin static files get `?v=` cache-busting like `/static`.
- Hub event type `ui` (`{area, rev}`) for a theme's own UI state.
- Tools: `archcheck.py --static-dir DIR` checks a theme's static tree (plus line length ≤ 120 and z-index tokens); `uishot.py --ui NAME`.

## 0.1.0 — 2026-10-14

First public release.

- Web control plane for one Linux host: projects, git, tmux sessions and
  coding agents (Claude Code, Codex, Devin, Cursor Agent, Antigravity).
- Agent Inbox, live chat view with composer, launch / fork / resume / exit,
  Quick chat, Builder with a sandboxed live preview.
- Projects with git status and actions (remote `gitdev`), TODO, backlog,
  plans and a files browser. Issue tracker in SQLite with the `pmctl` CLI.
- Servers page: host resources, processes, Docker containers, libvirt VMs and
  TCP checks of other hosts (`servers` in `pm.toml`).
- ntfy notifications and health alerts (off until you set an ntfy URL).
- Security: token or passkey login, optional TOTP unlock, CSRF + Origin
  checks, tiers, protected tmux sessions, fail-closed audit log, masked pane
  text.
- Host-neutral defaults: binds `127.0.0.1` only, `base_url`
  `http://127.0.0.1:8093`, no pv links, no extra projects, no git hosts, ntfy
  off, UTC display, no protected VM names.
- Docs: install, config reference, security model. Bundled extras: systemd
  unit, nginx example, Docker proxy + ntfy stack, `tmux-agents`.

